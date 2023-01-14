---
layout:     post
title:      "K8s 队列之基本队列实现"
date:       2020-03-21 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

这里的基本队列是指实现了如下接口的队列，代码路径为：`client-go/util/workqueue/queue.go`，区别于`DelayingInterface`，以及`RateLimitingInterface`，`DelayingInterface`实现延时加入队列的功能，`RateLimitingInterface`要配合限速器使用，实现了加入队列时的速率控制。后两者都内嵌了`Interface`，是基于此队列实现的。

```go
type Interface interface {
	Add(item interface{})
	Len() int
	Get() (item interface{}, shutdown bool)
	Done(item interface{})
	ShutDown()
	ShuttingDown() bool
}
```
先介绍下接口包含的方法：
* Add: 添加一个元素到队列，可以是任意类型，但是实际上只添加K8s资源的key（由namespace和name组成的字符串），具体处理某个元素时，再从informer cache中根据key取出元素进行处理。
* Len: 返回`queue`队列的长度。
* Get: 获取`queue`队列头部的一个元素，将此元素从`queue`中删除。
* Done: 标记一个元素刚刚被处理完了，从`processing`集合中删除，同时，如果`dirty`集合中有此元素，则添加到`queue`队列中
* ShutDown: 关闭队列
* ShuttingDown: 查询队列是否关闭

K8s代码里对此接口的实现结构体是`Type`，相关字段如下。核心字段就是一个队列`queue`，两个set`dirty`、`processing`。之所以设置这么多集合，是为了**保证一个元素在同一个时刻，只有一个worker在处理（也就是一个controller goroutine）**，在有多个worker的controller中，会有多个worker从queue中取元素。在有一个worker在处理一个元素的时候，其他worker不会拿到这个元素，即使这个元素需要再被处理。因为只要有worker在处理元素，就会把这个元素添加到`dirty`集合中。

另外需要注意，这个queue并不完全是FIFO的，比如我们有两个元素A, B，这两个元素发生的事件的顺序为A1、A2、B1，当A2发生的时候，A1还在处理，此时A2只能被添加到`dirty`集合中，假设B1事件又发生了，这时A1事件还在处理，因为`processing`集合中没有B的key，因此B可以直接被加入到`queue`队列中，等到A1处理完了，再把A2从`processing``dirty`中删除，并添加到`queue`中， 因此是先处理B1，再处理A2。

```go
type Type struct {
	// queue defines the order in which we will work on items. Every
	// element of queue should be in the dirty set and not in the
	// processing set.
	// 一个有序队列，
	queue []t
	// dirty defines all of the items that need to be processed.
	dirty set
	// Things that are currently being processed are in the processing set.
	// These things may be simultaneously in the dirty set. When we finish
	// processing something and remove it from this set, we'll check if
	// it's in the dirty set, and if so, add it to the queue.
	processing set
	cond *sync.Cond
	shuttingDown bool
	metrics queueMetrics
	unfinishedWorkUpdatePeriod time.Duration
	clock                      clock.Clock
}

type empty struct{}
type t interface{}
type set map[t]empty
```

下面具体分析一下每个方法的实现，顺便说一下，`Type`的所有操作都是加锁的，不管是读操作还是写操作，因此也是线程安全的，三个集合`queue`、`dirty`、`processing`在同一时刻，只能有一个goroutine在处理，所以能保证三个集合状态的一致性。

#### Add
Add标记一个元素需要被处理，当调用`Add`的时候，所添加的元素一定被添加到`dirty`集合中，如果当前被添加的元素正在被处理（也就是说在`processing`集合中）那么这个元素不需要被添加到`queue`队列中了，因为当前正在被某个goroutine处理的元素，在处理完调用`Done`方法时，会检查该元素是否在`processing`集合中，如果在`processing`集合中，则将其添加到`queue`队列中。

如果不在`processing`集合中，则添加到`queue`队列中。

另外，如果有goroutine阻塞在`Get`调用，则调用`q.cond.Signal`唤醒一个goroutine。
```go
func (q *Type) Add(item interface{}) {
	q.cond.L.Lock()
	defer q.cond.L.Unlock()
	if q.shuttingDown {
		return
	}
	if q.dirty.has(item) {
		return
	}

	q.metrics.add(item)

	q.dirty.insert(item)
	if q.processing.has(item) {
		return
	}

	q.queue = append(q.queue, item)
	q.cond.Signal()
}
```

#### Get
Get是一个阻塞式的调用，如果queue中没有元素可以取，则调用`sync.Cond.Wait()`进行阻塞（注意Wait要写在一个for循环中，因为被唤醒之后，还要争锁）。

Get从`queue`中取出第一个元素，并加入到`processing`集合中，表示这个元素当前正在被某个goroutine处理。另外如果`dirty`中有此元素，则从`dirty`中将元素删除。
```go
func (q *Type) Get() (item interface{}, shutdown bool) {
	q.cond.L.Lock()
	defer q.cond.L.Unlock()
	for len(q.queue) == 0 && !q.shuttingDown {
		q.cond.Wait()
	}
	if len(q.queue) == 0 {
		// We must be shutting down.
		return nil, true
	}

	item, q.queue = q.queue[0], q.queue[1:]

	q.metrics.get(item)

	q.processing.insert(item)
	q.dirty.delete(item)

	return item, false
}
```
#### Done
标记一个元素刚刚被处理完了，因为这个元素刚刚在被处理，所以在`processing`集合中，处理完了，需要从此集合中删除。另外检查`dirty`集合，如果在处理的时候这个元素又被添加进来了，会被放到`dirty`集合中，如果`dirty`集合中有此元素，则添加到`queue`队列中。

在处理完一个元素的时候，不管处理成功还是失败，都要调用`Done`来标记此次处理结束，如果处理失败，可以选择`Forget`忽略元素或者使用接口`AddRateLimited`重新加入到队列中，下次继续处理。

```go
func (q *Type) Done(item interface{}) {
	q.cond.L.Lock()
	defer q.cond.L.Unlock()

	q.metrics.done(item)

	q.processing.delete(item)
	if q.dirty.has(item) {
		q.queue = append(q.queue, item)
		q.cond.Signal()
	}
}
```

#### ShutDown
调用`q.cond.Broadcast`唤醒所有的goroutine，被唤醒的goroutine不需要再争夺锁。同时修改`shuttingDown`标志位。
```go
func (q *Type) ShutDown() {
	q.cond.L.Lock()
	defer q.cond.L.Unlock()
	q.shuttingDown = true
	q.cond.Broadcast()
}
```