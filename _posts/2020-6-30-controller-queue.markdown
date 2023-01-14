---
layout:     post
title:      "K8s 队列之基本 Controller 设计模式"
date:       2020-03-21 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - K8s
---

K8s Controller的一般工作原理是监听资源的变化，将事件放到本地队列里，然后对此队列进行同步。以[sig-storage-lib-external-provisioner](https://github.com/kubernetes-sigs/sig-storage-lib-external-provisioner)为例，简单分析一个K8s Controller的工作原理。

##### 一般工作原理
一般有下面步骤，算是K8s controller的编程模式，这里只关注队列的一些操作，对于informer这里不会介绍，会在其他文章里总结一下。
###### 队列声明
使用`RateLimitingInterface`声明一个队列，这个接口内嵌了`DelayingInterface`，而后者又内嵌了队列包中的`Interface`接口。概括地讲，`Interface`是一个队列基本队列操作的实现，包括添加、获取、去重（防止多个worker同时处理一个元素）等，可以参考[K8s队列之基本队列实现](https://loverhythm1990.github.io/2020/03/21/base-queue/)。`DelayingInterface`添加一个延时添加元素的方法`AddAfter(item interface{}, duration time.Duration)`，在一个元素处理失败时，可以通过这个方法，对这个元素进行退避。`RateLimitingInterface`接口主要是通过限速器定义退避的时间。
```go
claimQueue  workqueue.RateLimitingInterface
```

###### 队列初始化
这里先定义了一个`rateLimiter`限速器，`ExponentialFailureRateLimiter`是一个指数退避限速器，`BucketRateLimiter`是一个令牌桶限速器。`MaxOfRateLimiter`限速器是一个组合限速器（由多个限速器组成），取这多个限速器的最长延时时间作为返回值，也就是取最坏的等待时长。

```go
//"k8s.io/client-go/util/workqueue"

var rateLimiter workqueue.RateLimiter
rateLimiter = workqueue.NewMaxOfRateLimiter(
    workqueue.NewItemExponentialFailureRateLimiter(15*time.Second, 1000*time.Second),
    &workqueue.BucketRateLimiter{Limiter: rate.NewLimiter(rate.Limit(10), 100)},
)
controller.claimQueue = workqueue.NewNamedRateLimitingQueue(rateLimiter, "claims")
```
###### 往队列里丢事件
通过事件处理函数，在丢进队列之前，可以先做一些过滤。
```go
claimHandler := cache.ResourceEventHandlerFuncs{
    AddFunc:    func(obj interface{}) { controller.enqueueClaim(obj) },
    UpdateFunc: func(oldObj, newObj interface{}) { controller.enqueueClaim(newObj) },
    DeleteFunc: func(obj interface{}) {},
}

controller.claimInformer = informer.Core().V1().PersistentVolumeClaims().Informer()
// 注册事件处理函数
controller.claimInformer.AddEventHandler(claimHandler)

func (ctrl *ProvisionController) enqueueClaim(obj interface{}) {
	uid, err := getObjectUID(obj)
	if err != nil {
		utilruntime.HandleError(err)
		return
	}
	ctrl.claimQueue.Add(uid) // 调用Add方法添加到队列中
}
```
###### 启动worker
先指定worker的线程数，一般为1，有几个worker就启动几个goroutine。每个goroutine都试图从队列中取出元素，并调用`syncClaimHandler`对元素进行处理
```go
// run是控制器的主程序，也就是选主成功之后运行的程序，在这个程序结束时，关闭队列
run := func(ctx context.Context) {
    defer ctrl.claimQueue.ShutDown()

    for i := 0; i < ctrl.threadiness; i++ {
        // 启动worker线程
        go wait.Until(func() { ctrl.runClaimWorker(ctx) }, time.Second, ctx.Done())
    }
}
// 运行在单独的goroutine里面的
func (ctrl *ProvisionController) runClaimWorker(ctx context.Context) {
	for ctrl.processNextClaimWorkItem(ctx) {
        // 无限循环，直到队列close return false
    }
}

func (ctrl *ProvisionController) processNextClaimWorkItem(ctx context.Context) bool {
	obj, shutdown := ctrl.claimQueue.Get()
	if shutdown {
		return false
	}

	err := func() error {
		// 对worker添加超时，如果超时了，timeout context的Done channel就会被关闭
		if ctrl.provisionTimeout != 0 {
			timeout, cancel := context.WithTimeout(ctx, ctrl.provisionTimeout)
			defer cancel()
			ctx = timeout
        }
        // obj元素不管处理失败还是成功，都要调用Done方法标记处理完成，Done将元素从队列的processing集合中
        // 移除，并且检查dirty集合，如果dirty集合有此元素，重新添加到queue中。
		defer ctrl.claimQueue.Done(obj)
		var key string
		var ok bool
		if key, ok = obj.(string); !ok {
			ctrl.claimQueue.Forget(obj)
			return fmt.Errorf("expected string in workqueue but got %#v", obj)
		}

		if err := ctrl.syncClaimHandler(ctx, key); err != nil {
			ctrl.claimQueue.AddRateLimited(obj)
			return fmt.Errorf("error syncing claim %q: %s", key, err.Error())
		}

		ctrl.claimQueue.Forget(obj)
		return nil
	}()

	if err != nil {
		utilruntime.HandleError(err)
		return true
	}
	return true
}
```

##### 限速器
先关注`ItemExponentialFailureRateLimiter`，以及`BucketRateLimiter`。`MaxOfRateLimiter`限速器就是这两个限速器的组合。限速器接口定义如下：
```go
type RateLimiter interface {
	// When gets an item and gets to decide how long that item should wait
	When(item interface{}) time.Duration
	// Forget indicates that an item is finished being retried.  Doesn't matter whether its for perm failing
	// or for success, we'll stop tracking it
	Forget(item interface{})
	// NumRequeues returns back how many failures the item has had
	NumRequeues(item interface{}) int
}
```
有三个方法：
* When: 返回添加一个资源需要等待的时间
* Forget: 清除一个资源的退避记录，下次添加时从头开始退避，只在指数退避时起作用
* NumRequeues: 重试的次数，也就是累计重试的次数，一旦资源处理成功，这个会被置为0，也是只在指数退避时起作用

###### ItemExponentialFailureRateLimiter
`ItemExponentialFailureRateLimiter`实现了上述`RateLimiter`接口，其结构体定义为：
```go
type ItemExponentialFailureRateLimiter struct {
	failuresLock sync.Mutex
	failures     map[interface{}]int

	baseDelay time.Duration
	maxDelay  time.Duration
}
```
看上去还是比较简单，字段`baseDelay`表示最开始的退避时间，`maxDelay`表示最大退避时间，`failures map[interface{}]int`记录元素的失败次数，通过失败次数来记录退避时间，这个map的key类型为`interface{}`，一般也为k8s资源的key，即namespace和name组成的字符串。

**when**

计算方式如下，假设当前元素时第k次失败，则需要退避的时间为`baseDelay * math.Pow(2, k-1)`，并返回这个时间。此外`When`累计元素的失败次序，将`failure`的value累计1，这里顺便提一下，调用`Forget`方法时会清空这里的失败计数，如果调用了`Forget`方法，下次再调用限速队列的`AddRateLimited`方法时，将从`baseDelay`开始计数。
```go
func (r *ItemExponentialFailureRateLimiter) When(item interface{}) time.Duration {
	r.failuresLock.Lock()
	defer r.failuresLock.Unlock()
	exp := r.failures[item]
	r.failures[item] = r.failures[item] + 1

	// The backoff is capped such that 'calculated' value never overflows.
	backoff := float64(r.baseDelay.Nanoseconds()) * math.Pow(2, float64(exp))
	if backoff > math.MaxInt64 {
		return r.maxDelay
	}
	calculated := time.Duration(backoff)
	if calculated > r.maxDelay {
		return r.maxDelay
	}
	return calculated
}
```

**Forget**

`Forget`方法只是清空元素的失败计数，下次将元素加入队列时，从`baseDelay`开始计数。一般controller的`syncHandler`在处理元素成功时，需要调用`Forget`方法，表示这个元素不退避。在一些不需要controller处理元素的的情况，也需要调用`Forget`方法，（比如上面的`key.(string)`类型转换不成功）
```go
func (r *ItemExponentialFailureRateLimiter) Forget(item interface{}) {
	r.failuresLock.Lock()
	defer r.failuresLock.Unlock()
	delete(r.failures, item)
}
```
###### BucketRateLimiter
`BucketRateLimiter`实现较为简单，其结构定义如下，一般跟指数退避配合使用，用来控制全局的入队速率，其实现是跟具体某个资源没有关系的，也就是说他不关心要对哪个资源限速，所有元素一视同仁。
```go
type BucketRateLimiter struct {
	*rate.Limiter
}
```
其实现如下：
```go
func (r *BucketRateLimiter) When(item interface{}) time.Duration {
	return r.Limiter.Reserve().Delay()
}
func (r *BucketRateLimiter) NumRequeues(item interface{}) int { return 0 }
func (r *BucketRateLimiter) Forget(item interface{}) {}
```

##### 限速队列
文中一开始提到了`RateLimitingInterface`队列接口，其定义如下，是一个带限速的队列。
```go
type RateLimitingInterface interface {
	DelayingInterface

	// AddRateLimited adds an item to the workqueue after the rate limiter says it's ok
	AddRateLimited(item interface{})

	// Forget indicates that an item is finished being retried.  Doesn't matter whether it's for perm failing
	// or for success, we'll stop the rate limiter from tracking it.  This only clears the `rateLimiter`, you
	// still have to call `Done` on the queue.
	Forget(item interface{})

	// NumRequeues returns back how many times the item was requeued
	NumRequeues(item interface{}) int
}
```
在上一小节提到了限速器，我们看到限速器的三个方法跟上面三个方法很类似，其实队列的实现，也是直接调用限速器的三个方法：
```go
// rateLimitingType wraps an Interface and provides rateLimited re-enquing
type rateLimitingType struct {
	DelayingInterface
	rateLimiter RateLimiter
}

// AddRateLimited AddAfter's the item based on the time when the rate limiter says it's ok
func (q *rateLimitingType) AddRateLimited(item interface{}) {
    // 首先通过限速器获得时间，然后隔多少时间之后再加入
	q.DelayingInterface.AddAfter(item, q.rateLimiter.When(item))
}

func (q *rateLimitingType) NumRequeues(item interface{}) int {
	return q.rateLimiter.NumRequeues(item)
}

func (q *rateLimitingType) Forget(item interface{}) {
	q.rateLimiter.Forget(item)
}
```