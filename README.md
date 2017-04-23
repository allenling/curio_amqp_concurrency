# curio_amqp_concurrency

使用curio写的一个接收amqp消息, 分发消息的小工具. 也算是对https://github.com/allenling/simple-concurrency-with-amqp这个项目的小改进.

并没考虑rabbitmq离线的情况下, 重连或者关闭客户端. 只有客户端主动断开连接的操作.

发送的消息格式是json，内容是{"func": func, "args": [], "kwargs": {}}

运行：

python(>=3.5) run.py --workers WORKERS --task_path TASK_PATH --worker_timeout WORKER_TIMEUT --amqp_url AMQP_URL

* --workers WORKERS
  默认是1
* --task_path TASK_PATH
  默认是curio_amqp_concurrency.tasks
* --worker_timeout WORKER_TIMEOUT
  默认是30秒
* --amqp_url
  AMQP_URL   默认是amqp://testuser:testuser@localhost:5672/


# Python3中异步(asynchronous)的个人理解

基于协程使得得程序有能暂停，重启的的能力。

遇到IO的时候，协程直接暂停，通知上层对象(框架)去安排IO操作，上层对象用epoll等事件通知机制来管理IO操作，一旦epoll通知了哪些IO完成，上层对象负责将对应的协程给重新启动起来.

所以，协程的挂起仅仅是基础，需要开发者自己取实现IO操作的通知机制，asyncio和curio就是这样的角色，他们帮协程安排好IO操作，然后一旦IO完成，则重启协程.

所以整个asyncio/curio还是基于epoll的事件通知机制的.

## 一个典型例子

http://aosabook.org/en/500L/a-web-crawler-with-asyncio-coroutines.html

文中Guido解释了协程的实现方式，简单来说就是Python栈帧是堆内存，可以抽离与当前调用栈存在，并且生成器对象保存了当前执行到哪一步.

而PEP342中说明通过加强生成器，也就是添加send, throw和close方法，来实现了基于生成器的协程. 

在上面的文章中，Guido用一个爬虫的例子，解释了Python3中异步的基本思路是，在一个协程中，生成一个future对象，当程序遇到IO，想epoll注册一个FD和handler， 这个handler的作用是调用future.set_result来设置future的结果.

然后yield这个futur对象. 上层将一个名为setp的函数加入到future的回调列表中，一旦IO完成，handler调用future.set_result, 就是设置future的结果为IO返回值，同时调用future的回调函数.

也就是会调用到setp函数，setp函数则负责重新启动协程.


## 使用async/await或types.coroutine/asyncio.coroutine或者yield from定义协程

原文: http://www.snarky.ca/how-the-heck-does-async-await-work-in-python-3-5

详解: https://github.com/allenling/LingsKeep/blob/master/async_await_yield_from.rst

简单来说，async/await定义的就是协程，而用types.coroutine/asyncio.coroutine + yield from定义的就是基于生成器的协程, yield from就是生成器，但是
因为生成器跟协程有一样的API(send, throw, close)，所以可以当协程用

co_flag的区别: async/await定义的协程，其co_flag上是带有CO_COROUTINE

而使用yield from配合types.coroutine/asyncio.coroutine定义的协程的co_flag带有CO_ITERABLE_COROUTINE

而一般的生成器的co_flag带有的是CO_GENERATOR

字面上一个就是协程，一个是可迭代协程，也就是基于生成器的协程了

还有opcode的区别

await的opcode的是GET_AWAITABLE, 接受awaitable对象

yield from的opcode是GET_YIELD_FROM_ITER, 也就是说yield from可以接协程或者生成器或者可迭代对象，并且yield from后面跟协程的条件是yield from在一个用
types.coroutine/asyncio.coroutine装饰的函数内部



## 关于asyncio和curio设计上的区别

https://vorpus.org/blog/some-thoughts-on-asynchronous-api-design-in-a-post-asyncawait-world/#timeouts-and-cancellation

这里是我个人的理解https://github.com/allenling/LingsKeep/blob/master/some_throughts_on_async_await_api_design理解.rst


