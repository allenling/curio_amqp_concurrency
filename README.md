# curio_amqp_concurrency

使用curio写的一个接收amqp消息, 分发消息的小工具. 也算是对https://github.com/allenling/simple-concurrency-with-amqp这个项目的小改进.

并没考虑rabbitmq离线的情况下, 重连或者关闭客户端. 只有客户端主动断开连接的操作.

发送的消息格式是json，内容是{"func": func, "args": [], "kwargs": {}}

运行：

python(>=3.5) run.py --workers workers --task_path task_path --worker_timeout worker_timeut --amqp_url amqp_url

--workers WORKERS     默认是1
--task_path TASK_PATH
                      默认是curio_amqp_concurrency.tasks
--worker_timeout WORKER_TIMEOUT
                      默认是30秒
--amqp_url AMQP_URL   默认是amqp://testuser:testuser@localhost:5672/


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


## 关于async/await和yield from

http://www.snarky.ca/how-the-heck-does-async-await-work-in-python-3-5

文中解释了async/await和yield from的区别已经async/await是做什么工作的.

简单来说

await和yield from的区别不大, 都是对子生成器的委托(PEP380). 都是for i in subgenrator: yield i操作，只是await更严格，只能在async定义的函数中出现，并且后面只能跟awaitable对象(定义了__await__方法，并且返回一个可迭代对象).
而yield from没有这些限制.

async/await就是清楚的区分生成器和协程，而types.coroutine装饰器则是区分用于协程的和仅仅做迭代的生成器.

一个带有types.coroutine装饰器的生成器和一个async定义的函数在opcode上有一些区别

types.coroutine的作用是将CO_ITERABLE_COROUTINE的code flag加入到func的codeobject中(也就是func.__code__), 然后VM在看到

CO_ITERABLE_COROUTINE的flag， 就调用GET_YIELD_FROM_ITER， GET_YIELD_FROM_ITER检查yield from是否跟着参数arg是否是一个生成器或者协程， 如果不是， 则调用iter(arg)


await在VM的opcode是GET_AWAITABLE, 也就是调用awaitable对象的__await__方法.


而await后面接的是awaitable对象的时候， VM的opcode就是GET_AWAITABLE, 由于__await__方法返回一个可迭代对象， 所以GET_AWAITABLE就是获取可迭代对象.

asynci/await就是一个API接口，提供了一个这样的约定: async定义的函数是协程，await则返回一个awaitable对象, awaitable对象中的__await__方法返回的对象可以迭代.



## 关于asyncio和curio设计上的区别

https://vorpus.org/blog/some-thoughts-on-asynchronous-api-design-in-a-post-asyncawait-world/#timeouts-and-cancellation

这里是我个人的理解https://github.com/allenling/LingsKeep/blob/master/some_throughts_on_async_await_api_design理解.rst


