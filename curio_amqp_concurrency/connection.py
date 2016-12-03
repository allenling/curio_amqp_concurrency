# coding=utf-8
'''
使用curio和pika来建立amqp连接

仅仅是建立连接,解析msg而已, 对很多情况没有做判断
'''
import pika

import curio


CLIENT_INFO = {'platform': 'Python 3.5.2', 'product': 'curio amqp prefork', 'version': '0.1.0'}


class AmqpUrlParser(object):

    def __init__(self, url):
        self.url = url
        self.protocol, rest = self.url.split('://')
        if self.protocol.lower() != 'amqp':
            raise Exception('not amqp protocol')
        user_and_pwd, address = rest.split('@')
        self.username, self.password = user_and_pwd.split(':')
        self.host, self.port = address.split(':')
        self.port, self.vhost = self.port.split('/')
        self.port = int(self.port)
        if self.vhost == '':
            self.vhost = '/'


class Exchange:
    def __init__(self, name):
        self.name = name


class Queue:
    def __init__(self, name):
        self.name = name


class Channel:
    def __init__(self, channel_number):
        self.channel_number = channel_number


class Connection:
    # rabbitmq frame max size is 131072
    MAX_DATA_SIZE = 131072

    def __init__(self, amqp_url, prefetch_count, master_queue):
        self.amqp_config = AmqpUrlParser(amqp_url)
        self.prefetch_count = prefetch_count
        self.master_queue = master_queue
        self.sock = None
        self.host = None
        self.port = None
        self.alive = True
        self.current_channel_number = 0
        self.channels = {}

    def get_next_channel_number(self):
        self.current_channel_number += 1
        return self.current_channel_number

    async def assert_recv_method(self, method_class):
        data = await self.sock.recv(self.MAX_DATA_SIZE)
        frame_obj = pika.frame.decode_frame(data)[1]
        assert isinstance(frame_obj.method, method_class)
        return frame_obj

    async def send_amqp_procotol_header(self):
        amqp_header_frame = pika.frame.ProtocolHeader()
        # wait for start
        await self.sock.sendall(amqp_header_frame.marshal())

    async def send_start_ok(self):
        start_ok_response = b'\0' + pika.compat.as_bytes(self.amqp_config.username) + b'\0' + pika.compat.as_bytes(self.amqp_config.password)
        start_ok_obj = pika.spec.Connection.StartOk(client_properties=CLIENT_INFO, response=start_ok_response)
        frame_value = pika.frame.Method(0, start_ok_obj)
        await self.sock.sendall(frame_value.marshal())

    async def send_tune_ok(self):
        # for now, do not want a heartbeat
        tunk = pika.spec.Connection.TuneOk(frame_max=self.MAX_DATA_SIZE)
        frame_value = pika.frame.Method(0, tunk)
        await self.sock.sendall(frame_value.marshal())

    async def send_connection_open(self):
        connection_open = pika.spec.Connection.Open(insist=True)
        frame_value = pika.frame.Method(0, connection_open)
        await self.sock.sendall(frame_value.marshal())

    async def connect(self):
        self.sock = await curio.open_connection(self.amqp_config.host, self.amqp_config.port)
        # send amqp header frame
        await self.send_amqp_procotol_header()
        # got start
        await self.assert_recv_method(pika.spec.Connection.Start)
        # send start ok back
        await self.send_start_ok()
        # got tune
        await self.assert_recv_method(pika.spec.Connection.Tune)
        # send tune ok
        await self.send_tune_ok()
        # and we send open
        await self.send_connection_open()
        # got openok
        await self.assert_recv_method(pika.spec.Connection.OpenOk)
        return

    async def open_channel(self):
        # send Channel.Open
        channel_number = self.get_next_channel_number()
        channel_open = pika.spec.Channel.Open()
        frame_value = pika.frame.Method(channel_number, channel_open)
        await self.sock.sendall(frame_value.marshal())
        # got Channel.Open-Ok
        frame_obj = await self.assert_recv_method(pika.spec.Channel.OpenOk)
        assert frame_obj.channel_number == channel_number
        channel_obj = Channel(channel_number=frame_obj.channel_number)
        self.channels[channel_number] = channel_obj
        return channel_obj

    async def declare_exchange(self, channel, name):
        exchange_declare = pika.spec.Exchange.Declare(exchange=name)
        frame_value = pika.frame.Method(channel, exchange_declare)
        await self.sock.sendall(frame_value.marshal())
        await self.assert_recv_method(pika.spec.Exchange.DeclareOk)
        return Exchange(name=name)

    async def declare_queue(self, channel, name):
        queue_declare = pika.spec.Queue.Declare(queue=name)
        frame_value = pika.frame.Method(channel, queue_declare)
        await self.sock.sendall(frame_value.marshal())
        await self.assert_recv_method(pika.spec.Queue.DeclareOk)
        return Queue(name=name)

    async def bind(self, channel, exchange, queue, routing_key):
        queue_bind = pika.spec.Queue.Bind(queue=queue, exchange=exchange, routing_key=routing_key)
        frame_value = pika.frame.Method(channel, queue_bind)
        await self.sock.sendall(frame_value.marshal())
        await self.assert_recv_method(pika.spec.Queue.BindOk)

    async def update_qos(self, channel):
        qos_obj = pika.spec.Basic.Qos(prefetch_count=self.prefetch_count)
        frame_value = pika.frame.Method(channel, qos_obj)
        await self.sock.sendall(frame_value.marshal())
        await self.assert_recv_method(pika.spec.Basic.QosOk)

    async def send_msg(self, data):
        msgs = []
        tmp = {}
        while data:
            count, frame_obj = pika.frame.decode_frame(data)
            data = data[count:]
            if isinstance(frame_obj, pika.frame.Header):
                continue
            if isinstance(frame_obj, pika.frame.Body):
                tmp['data'] = frame_obj.fragment.decode("utf-8")
                msgs.append(tmp)
                tmp = {}
                continue
            if isinstance(frame_obj.method, pika.spec.Basic.Deliver):
                tmp = {'channel_number': frame_obj.channel_number,
                       'delivery_tag': frame_obj.method.delivery_tag,
                       'consumer_tag': frame_obj.method.consumer_tag,
                       'exchange': frame_obj.method.exchange,
                       'routing_key': frame_obj.method.routing_key}
        for msg in msgs:
            await self.master_queue.put(msg)

    async def start_consume(self, channel, queue):
        star_comsume = pika.spec.Basic.Consume(queue=queue, consumer_tag=str(channel))
        frame_value = pika.frame.Method(channel, star_comsume)
        await self.sock.sendall(frame_value.marshal())
        data = await self.sock.recv(self.MAX_DATA_SIZE)
        count, frame_obj = pika.frame.decode_frame(data)
        assert isinstance(frame_obj.method, pika.spec.Basic.ConsumeOk)
        if len(data) > count:
            await self.send_msg(data[count:])
        try:
            while self.alive:
                data = await self.sock.recv(self.MAX_DATA_SIZE)
                await self.send_msg(data)
        except curio.CancelledError:
            await self.close()

    async def ack(self, data):
        print ('ack %s' % data)
        # TODO: ack失败, 抛出错误, 返回
        ack = pika.spec.Basic.Ack(delivery_tag=data['delivery_tag'])
        frame_value = pika.frame.Method(data['channel_number'], ack)
        await self.sock.sendall(frame_value.marshal())

    async def close(self):
        # 等待队列为空, 这是因为还有任务需要ack
        await self.master_queue.join()
        # 关闭连接
        await self.sock.close()

