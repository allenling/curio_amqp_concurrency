# curio_amqp_concurrency

使用curio写的一个接收amqp消息, 分发消息的小工具

并没考虑rabbitmq离线的情况下, 重连或者关闭客户端. 只有客户端主动断开连接的操作.

