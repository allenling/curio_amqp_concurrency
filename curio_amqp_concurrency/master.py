import os
import time
import signal
import errno
import importlib
import json
import argparse

import curio

from .connection import Connection


class Worker(curio.workers.ProcessWorker):
    '''
    这里继承于curio的ProcessWorker
    不同的是, curio中worker是await等待结果返回
    这里需要分发任务到子进程之后, 不等待任务完成, 继续分发下一个任务
    所以, worker pool中需要spawn一个wait任务去监视子进程运行是否超时
    '''

    def __init__(self, age):
        self.age = age
        super(Worker, self).__init__(None)
        self._launch()

    async def apply(self, func, args=()):
        msg = (func, args)
        # 将任务发送到子进程之后就返回, 不等待结果返回
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
                    print ('no any worker wait for reap')
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
        '''
        wait没办法显式地去cancel, 所以需要在程序中加入对self.alive判断
        这里每0.5秒去检查self.alive, 每一个重新启动点都加上self.alive的判断
        一旦self.alive=False, wait协程需要停止
        暂时没找到更好的办法
        若没超时, 发送ack
        若超时, 发送ack, 不管self.alive是否为False
        若self.alive = False, 调用queue.task_done, 然后return
        一个必须的操作是queue.task_done, 这样才能最后关闭connection

        这里代码需要, 更清晰点
        '''
        func_data = data['data']
        end_time = time.time() + self.timeout
        sleep_time = 0.5
        while self.alive:
            try:
                success, result = await curio.timeout_after(sleep_time, worker.client_ch.recv())
            except curio.TaskTimeout:
                now = time.time()
                if now < end_time and self.alive:
                    sleep_time = end_time - now if end_time - now < 0.5 else sleep_time
                    continue
                if now >= end_time:
                    print ('{0}, {1}, {2} timeout'.format(func_data['func'], func_data['args'], func_data['kwargs']))
                    self.kill_worker(worker.process.pid)
                    break
                if not self.alive:
                    await master_queue.task_done()
                    return
            else:
                print ('{0}, {1}, {2}, success: {3}, result: {4}'.format(func_data['func'], func_data['args'], func_data['kwargs'], success, result))
                self.idle_workers.append(worker.process.pid)
                break
        await connection.ack(data)
        await master_queue.task_done()
        if self.alive:
            self.manage_workers()
        return

    async def apply(self, data, master_queue, connection):
        func, args = data['data']['func'], data['data']['args']
        worker = self.pool[self.idle_workers.pop(0)]
        # 将任务分发到一个worker中
        await worker.apply(func, args)
        print ('apply worker %s' % worker.process.pid)
        # spawn一个wait任务去监视子进程是否超时
        await curio.spawn(self.wait(worker, data, master_queue, connection))


class Master:
    def __init__(self, worker_nums, task_module, worker_timeout, amqp_url):
        self.task_module = importlib.import_module(task_module)
        self.worker_nums = worker_nums
        self.worker_timeout = worker_timeout
        self.amqp_url = amqp_url
        self.alive = True

    async def start(self):
        print ('master %s start' % os.getpid())
        # 下面都是建立amqp连接
        self.master_queue = curio.Queue()
        self.con = Connection(self.amqp_url, self.worker_nums, self.master_queue)
        await self.con.connect()
        channel = await self.con.open_channel()
        exchange = await self.con.declare_exchange(channel.channel_number, 'curio_amqp_exchange')
        queue = await self.con.declare_queue(channel.channel_number, 'curio_amqp_queue')
        await self.con.bind(channel.channel_number, exchange.name, queue.name, routing_key='curio_amqp')
        await self.con.update_qos(channel.channel_number)

        # 构建worker pool
        self.pool = WorkerPool(self.worker_nums, self.worker_timeout)

        # spawn接收amqp消息的任务和分发msg到worker的任务
        consume_task = await curio.spawn(self.con.start_consume(channel.channel_number, queue.name))
        fetch_task = await curio.spawn(self.fetch_amqp_msg())

        # 这里监听信号
        while self.alive:
            with curio.SignalQueue(*[signal.SIGTERM, signal.SIGINT, signal.SIGCHLD]) as sigQueue:
                sig = await sigQueue.get()
                print ('master got signal %s' % sig)
                if sig in [signal.SIGTERM, signal.SIGINT]:
                    self.alive = False
                    break
                elif sig == signal.SIGCHLD:
                    self.pool.reap_workers()
                    self.pool.manage_workers()
        # 需要退出, 则将connection和pool的alive置为False
        self.pool.alive = False
        self.con.alive = False
        try:
            # 等到任务结束
            await curio.timeout_after(1, consume_task.join())
            await curio.timeout_after(1, fetch_task.join())
        except curio.TaskTimeout:
            # 强制取消任务
            await consume_task.cancel()
            await fetch_task.cancel()
        # 杀死所有的子进程
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
    parser = argparse.ArgumentParser(description='基于curio的amqp分发app')
    parser.add_argument('--workers', type=int, default=1, help="默认是1")
    parser.add_argument('--task_path', default='curio_amqp_concurrency.tasks', help="默认是curio_amqp_concurrency.tasks")
    parser.add_argument('--worker_timeout', type=int, default=30, help="默认是30秒")
    parser.add_argument('--amqp_url', default='amqp://guest:guest@localhost:5672/', help="默认是amqp://guest:guest@localhost:5672/")
    args = parser.parse_args()
    print (type(args.workers))
    m = Master(args.workers, args.task_path, args.worker_timeout, args.amqp_url)
    try:
        curio.run(m.start())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
