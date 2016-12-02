# coding=utf-8
import os
import time
import signal
import errno
import importlib
import json

import curio

from .connection import Connection


class Worker(curio.workers.ProcessWorker):

    def __init__(self, age):
        self.age = age
        super(Worker, self).__init__()
        self._launch()

    async def apply(self, func, args=(), kwargs={}):
        msg = (func, args, kwargs)
        await self.client_ch.send(msg)


class WorkerPool:

    def __init__(self, nums, worker_timeout):
        self.timeout = worker_timeout
        self.nums = nums
        self.pool = {}
        self.age = 1
        self.idle_workers = []
        self.alive = True
        self.manage_workers()

    def manage_workers(self):
        if len(self.pool) < self.nums:
            while len(self.pool) < self.nums:
                worker = Worker(self.age)
                self.pool[worker.process.pid] = worker
                self.idle_workers.append(worker.process.pid)
                self.age += 1
                print ('spawn worker %s' % worker.process.pid)
        if len(self.pool) > self.nums:
            pids = sorted(list(self.pool.keys()))
            while len(self.pool) > self.nums:
                pid = pids.pop(0)
                self.kill_worker(pid)

    def kill_worker(self, wpid):
        print ('killing worker %s' % wpid)
        os.kill(wpid, signal.SIGKILL)
        self.pool.pop(wpid)
        if wpid in self.idle_workers:
            self.idle_workers.pop(self.idle_workers.index(wpid))

    def kill_all_workers(self):
        for wpid in list(self.pool.keys()):
                self.kill_worker(wpid)

    def reap_workers(self):
        try:
            while True:
                wpid, status = os.waitpid(-1, os.WNOHANG)
                if not wpid:
                    break
                print ('reap worker %s, exit with %s' % (wpid, status))
                if wpid in self.pool:
                    self.pool.pop(wpid)
                if wpid in self.idle_workers:
                    self.idle_workers.pop(self.idle_workers.index(wpid))
        except OSError as e:
            if e.errno != errno.ECHILD:
                raise

    async def wait(self, worker, data, master_queue, connection):
        func_data = data['data']
        end_time = time.time() + self.timeout
        sleep_time = 0.5
        while self.alive:
            try:
                success, result = await curio.timeout_after(sleep_time, worker.client_ch.recv())
            except curio.TaskTimeout:
                if not self.alive:
                    return
                now = time.time()
                if now < end_time:
                    sleep_time = end_time - now if end_time - now < 0.5 else sleep_time
                    continue
                print ('{0}, {1}, {2} timeout'.format(func_data['func'], func_data['args'], func_data['kwargs']))
                print ('worker %s shutdown' % worker.process.pid)
                self.kill_worker(worker.process.pid)
            else:
                print ('{0}, {1}, {2}, success: {3}, result: {4}'.format(func_data['func'], func_data['args'], func_data['kwargs'], success, result))
                self.idle_workers.append(worker.process.pid)
            self.manage_workers()
            await connection.ack(data)
            await master_queue.task_done()
            break
        return

    async def apply(self, data, master_queue, connection):
        func, args, kwargs = data['data']['func'], data['data']['args'], data['data']['kwargs']
        worker = self.pool[self.idle_workers.pop(0)]
        await worker.apply(func, args, kwargs)
        print ('apply worker %s' % worker.process.pid)
        await curio.spawn(self.wait(worker, data, master_queue, connection))


class Master:
    def __init__(self, worker_nums, task_module, worker_timeout=5, amqp_url='amqp://testuser:testuser@localhost:5672/'):
        self.task_module = importlib.import_module(task_module)
        self.worker_nums = worker_nums
        self.worker_timeout = worker_timeout
        self.amqp_url = amqp_url
        self.alive = True

    async def start(self):
        print ('master %s start' % os.getpid())
        self.master_queue = curio.Queue()
        self.con = Connection(self.amqp_url, self.worker_nums, self.master_queue)
        await self.con.connect()
        channel = await self.con.open_channel()
        exchange = await self.con.declare_exchange(channel.channel_number, 'curio_amqp_exchange')
        queue = await self.con.declare_queue(channel.channel_number, 'curio_amqp_queue')
        await self.con.bind(channel.channel_number, exchange.name, queue.name, routing_key='curio_amqp')
        await self.con.update_qos(channel.channel_number)
        self.pool = WorkerPool(self.worker_nums, self.worker_timeout)
        consume_task = await curio.spawn(self.con.start_consume(channel.channel_number, queue.name))
        fetch_task = await curio.spawn(self.fetch_amqp_msg())
        while self.alive:
            sig = await curio.SignalSet(*[signal.SIGTERM, signal.SIGINT, signal.SIGCHLD]).wait()
            print ('master got signal %s' % sig)
            if sig in [signal.SIGTERM, signal.SIGINT]:
                self.alive = False
                break
            elif sig == signal.SIGCHLD:
                self.pool.reap_workers()
                self.pool.manage_workers()
        self.pool.alive = False
        self.con.alive = False
        try:
            await curio.timeout_after(1, consume_task.join())
            await curio.timeout_after(1, fetch_task.join())
        except curio.TaskTimeout:
            await consume_task.cancel()
            await fetch_task.cancel()
        self.pool.kill_all_workers()
        print ('master gone')

    async def fetch_amqp_msg(self):
        while self.alive:
            data = await self.master_queue.get()
            data['data'] = json.loads(data['data'])
            print ('get %s' % data)
            data['data']['func'] = getattr(self.task_module, data['data']['func'])
            await self.pool.apply(data, self.master_queue, self.con)

def main():
    m = Master(1, 'curio_amqp_concurrency.curio_amqp_tasks')
    try:
        curio.run(m.start())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()

