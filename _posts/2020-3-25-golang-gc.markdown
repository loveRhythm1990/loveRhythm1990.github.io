---
layout:     post
title:      "Golang gc 概述"
date:       2020-03-25 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

本文分析的算法是根据go1.12版本。
分为三个阶段：
* Mark Setup - STW
* Marking - Concurrent
* Mark Termination - STW

#### Mark Setup - STW
垃圾收集开始的时候，第一件事是打开写屏障（Write Barrier），写屏障的目的是使收集器保护堆中数据的完整性，毕竟在收集器以及应用进程会并行工作。

为了打开写屏障，每个应用的goroutine都必须停下来，这个过程会很快，大概10-30微秒（microsecond）。让所有goroutine都能安全的停下来的方法是等待其发起函数调用，（这个应该是发起函数调用的时候，上下文比较容易保存），如果goroutine不发起函数调用呢？比如在执行一个无限for循环，这种情况下比较糟糕，执行for循环的这个goroutine不停下来，其他goroutine都停下来了，并且在等着它。这个跟golang调度器的抢占类似，只有在goroutine执行函数调用的时候才可以抢占。**在golang 1.14中实现了基于信号量的抢占**，这个参考文献中给出了一个链接，有空研究一下。

#### Marking - Concurrent
当打开写屏障之后，收集器开始执行Marking。此时收集器的第一件任务是占用20%的cpu资源，收集器与应用goroutine使用同样的P/M资源，现在它要占用原来的25%的P/M。Marking阶段就是要标记heap中所有正在使用的object。大概是这样工作的：查找所有存在的goroutine的stack，找root object（find root pointers to heap memory）；接下来收集器根据这些root object遍历heap中的object（能找到的就算是还在用）。

刚刚提到了收集器会占用25%的P/M来进行标记Marking，那么在标记阶段，其他的应用goroutine仍然是在运行的，这些应用goroutine占用了剩下的P/M，这里说的就是并发标记，因为收集器占用了25%的PM，所以性能还是有影响的，但是影响有限。

这里Ardan labs提出了一个问题：加入垃圾收集器还没有完成标记工作，但是heap已经用完了，这时候会发生什么？如果只是由于一个应用goroutine导致的heap用完怎么办？这个时候应该停止内存的申请速度，尤其是导致heap用完的那个goroutine。

当收集器决定它需要去停止内存分配的时候，它会征用（recruit）部分应用goroutine来协助标记工作，这个叫做`Marking Assist`，应用goroutine被征用来执行Marking Assist的时间跟它向heap中写入的数据量有关。Marking Assist有利的一方面是它加快了标记的速度。没有被征用的goroutine继续执行应用业务。收集器的一个优化目标就是减少Marking Assist goroutine的数量，如果上次垃圾收集过程中，征用了大量goroutine，那么下次垃圾收集会提早开始。

#### Mark Termination - STW
标记工作完成之后，下一个阶段就是Mark Termination。这个过程是Write Barrier被关闭，各种（various）清理工作开始执行，并且计算下次垃圾收集的目标。这个阶段是需要STW的，大概需要60-90微秒，Ardan labs说不STW也行的，但是STW的代码比较简单，得不偿失。这里说了一些various clean work，这个还不是很清楚（应该是指GC相关资源的清理回收，而不是sweep操作）。这个Mark Termination接收之后，应用goroutine又可以拿到P/M愉快的执行了。

#### Sweeping - Concurrent
收集完就开始清扫（Sweeping），就是把heap中没有被标记为正在使用的Object清理掉。这个过程发生在**应用goroutine试图在heap中分配内存的时候**，Sweeping带来的时延是被统计到内存分配中的，并没有跟GC的时延绑定在一起。

`runtime.mallocgc`是向堆内存申请空间时的函数调用，其中`runtime.(*mcache).nextFree`是导致Sweep的函数调用。

除了调用`runtime.mallocgc`时会发生清理工作，另外两个触发GC的时机是:
* runtime.sysmon和runtime.forcegchelper 后台定时检查和垃圾收集
* runtime.GC 用户手动触发垃圾收集
* runtime.mallocgc 申请内存时根据堆大小触发垃圾收集。

另外两个没什么好讲的，稍微说一下mallocgc，golang运行时将堆上的对象按大小分成tiny对象/small对象/large对象，这三类对象的创建可能都会触发新的垃圾收集循环：
```go
func mallocgc(size uintptr, typ *_type, needzero bool) unsafe.Pointer {
	shouldhelpgc := false
	...
	if size <= maxSmallSize {
		if noscan && size < maxTinySize {
			...
			v := nextFreeFast(span)
			if v == 0 {
				v, _, shouldhelpgc = c.nextFree(tinySpanClass)
			}
			...
		} else {
			...
			v := nextFreeFast(span)
			if v == 0 {
				v, span, shouldhelpgc = c.nextFree(spc)
			}
		  ...
		}
	} else {
		shouldhelpgc = true
		...
	}
	...
	if shouldhelpgc {
		if t := (gcTrigger{kind: gcTriggerHeap}); t.test() {
			gcStart(t)
		}
	}

	return x
}
```
1. 当前线程的内存管理单元中不存在空闲空间时，创建tiny对象和小对象需要调用runtime.mcache.nextFree方法从中心缓存（mcenter）或者页堆（mheap）中获取新的管理单元，在这个时候可能会触发垃圾收集。
2. 当用户程序申请分配32KB以上的大对象时，一定会构建`runtime.gcTrigger`结构体尝试触发垃圾回收。

根据戴维斯[博客](https://mp.weixin.qq.com/s?__biz=MzU5NTAzNjc3Mg==&mid=2247484261&idx=1&sn=b17ce0394a6da20aff6801c64cf5835d&chksm=fe795c6ec90ed578c6e820f30848fcc2713320f645431aa50b6b77bc2f0e4386546f4553acf3&scene=0&xtrack=1#rd):

通过堆内存触发垃圾收集需要比较runtime.mstats中的两个字段 — 表示垃圾收集中存活对象字节数的`heap_live`和表示触发标记的堆内存大小的 `gc_trigger`;当内存中存活的对象字节数大于触发垃圾收集的堆大小时，新一轮的垃圾收集就会开始。在这里，我们将分别介绍这两个值的计算过程：
* heap_live — 为了减少锁竞争，运行时只会在中心缓存分配或者释放内存管理单元以及在堆上分配大对象时才会更新；
* gc_trigger — 在标记终止阶段调用 runtime.gcSetTriggerRatio 更新触发下一次垃圾收集的堆大小；


上面这些描述说明了GC的时候会发生什么事情，接下来会介绍决定GC什么时候开始的一个重要指标：`GC Percentage`

#### GC Percentage
golang runtime有一个配置选项叫做GC Percentage，默认被配置为100。这个值表示了在下次GC之前多少比例的内容可以被分配。设置为100%就表示：基于上次Collection完成之后存货的object数量，下次有100%的这些数量被分配前，必须要启动GC。这个可以参考下图：

![java-javascript](/img/in-post/go-gc-lab/Picture1.png)

#### GC Trace
使用环境变量`GODEBUG`并设置`gctrace=1`可以用来答应gc信息，每次垃圾收集器运行时都会打印GC信息，比如：
```s
GODEBUG=gctrace=1 ./app

gc 1405 @6.068s 11%: 0.058+1.2+0.083 ms clock, 0.70+2.5/1.5/0+0.99 ms cpu, 7->11->6 MB, 10 MB goal, 12 P

gc 1406 @6.070s 11%: 0.051+1.8+0.076 ms clock, 0.61+2.0/2.5/0+0.91 ms cpu, 8->11->6 MB, 13 MB goal, 12 P

gc 1407 @6.073s 11%: 0.052+1.8+0.20 ms clock, 0.62+1.5/2.2/0+2.4 ms cpu, 8->14->8 MB, 13 MB goal, 12 P
```
下面是对GC日志的分析：
```s
gc 1405 @6.068s 11%: 0.058+1.2+0.083 ms clock, 0.70+2.5/1.5/0+0.99 ms cpu, 7->11->6 MB, 10 MB goal, 12 P

// General
gc 1404     : The 1404 GC run since the program started，应该是第1404次垃圾回收
@6.068s     : 程序启动6秒了
11%         : 有11%的cpu消耗在了GC上

// Wall-Clock (挂钟走过的时间，这里表示的是实际的时间)
gc 1404     : The 1404 GC run since the program started
@6.068s     : 程序启动6秒了
11%         : 有11%的cpu消耗在了GC上

// Wall-Clock
0.058ms     : STW        : Mark Start       - Write Barrier on（打开写屏障）
1.2ms       : Concurrent : Marking
0.083ms     : STW        : Mark Termination - Write Barrier off and clean up（关闭写屏障、清理工作）

// CPU Time  （这个CPU time跟上面的 wall-clock有什么区别？）
0.70ms      : STW        : Mark Start
2.5ms       : Concurrent : Mark - Assist Time (GC performed in line with allocation)
1.5ms       : Concurrent : Mark - Background GC time
0ms         : Concurrent : Mark - Idle GC time
0.99ms      : STW        : Mark Term

// Memory
7MB         : Heap memory in-use before the Marking started
11MB        : Heap memory in-use after the Marking finished
6MB         : Heap memory marked as live after the Marking finished
10MB        : Collection goal for heap memory in-use after Marking finished

// Threads
12P         : Number of logical processors or threads used to run Goroutines
```

在Ardan labs的第二篇文章中[Garbage Collection In Go : Part II - GC Traces](https://www.ardanlabs.com/blog/2019/05/garbage-collection-in-go-part2-gctraces.html)介绍了一个通过分析GC trace，以及pprof来提升效率，以及减少内存分配的例子。具体是这样的：

运行一个web服务，分别设置环境变量`GOGC=off`以及`GOGC=on`来向这个web服务发送10000个请求，在`GOGC=on`的时候（就是默认不设置，还打开环境变量`GODEBUG=gctrace=1`来看每次GC的日志）。分析pprof时，使用的连接为`go tool pprof http://localhost:5000/debug/pprof/allocs`，（这个需要在代码的头部加入这个引入`import _ "net/http/pprof"`），然后运行top查看使用内存最多的方法，输入如下：
```s
(pprof) top 6 -cum
Showing nodes accounting for 0.56GB, 5.84% of 9.56GB total
Dropped 80 nodes (cum <= 0.05GB)
Showing top 6 nodes out of 51
      flat  flat%   sum%        cum   cum%
         0     0%     0%     4.96GB 51.90%  net/http.(*conn).serve
    0.49GB  5.11%  5.11%     4.93GB 51.55%  project/service.handler
         0     0%  5.11%     4.93GB 51.55%  net/http.(*ServeMux).ServeHTTP
         0     0%  5.11%     4.93GB 51.55%  net/http.HandlerFunc.ServeHTTP
         0     0%  5.11%     4.93GB 51.55%  net/http.serverHandler.ServeHTTP
    0.07GB  0.73%  5.84%     4.55GB 47.63%  project/search.rssSearch
```
发现问题出在`search.rssSearch`方法，使用`list`命令查看这个方法的详细信息
```s
(pprof) list rssSearch
Total: 9.56GB
ROUTINE ======================== project/search.rssSearch in project/search/rss.go
   71.53MB     4.55GB (flat, cum) 47.63% of Total


         .          .    117:	// Capture the data we need for our results if we find ...
         .          .    118:	for _, item := range d.Channel.Items {
         .     4.48GB    119:		if strings.Contains(strings.ToLower(item.Description), strings.ToLower(term)) {
   48.53MB    48.53MB    120:			results = append(results, Result{
         .          .    121:				Engine:  engine,
         .          .    122:				Title:   item.Title,
         .          .    123:				Link:    item.Link,
         .          .    124:				Content: item.Description,
         .          .    125:			})
```
发现有这么一行代码`strings.Contains(strings.ToLower(item.Description), strings.ToLower(term))`占用了很多内存。文章没有给出代码是怎么改的，主要在强调不要再for循环中调用`strings.ToLower`，要拿到for循环外面去，因为这个方法会为字符串重新分配堆内存。文章最后给了修改前后，以及是否开启GC的时间对比（从图中可以看出，不开启GC程序跑起来还是很快的）

![java-javascript](/img/in-post/go-gc-lab/101_figure6.png)

#### GC Pacing
收集器有一个`pacing`算法，（pace翻译过来是步伐，指运行垃圾收集器的时机与周期），用来决定垃圾收集什么时候开始，这个算法依赖一个feedback loop，这个feedback loop主要工作有：收集运行的goroutine的信息；以及应用申请heap的stress，这个强度可以理解为大小以及频率吧（Stress can be defined as how fast the application is allocating heap memory within a given amount of time），stress决定了垃圾收集器运行的pace。

垃圾收集器开始之前，它首先预估一下接下来的垃圾收集需要多长时间，一旦垃圾收集开始，应用gorotine就可能受影响了。文章的这部分一直强调，通过增大pace来延迟GC并不是减少GC的措施。

Ardan labs关于GC的第三篇文章[Garbage Collection In Go : Part III - GC Pacing](https://www.ardanlabs.com/blog/2019/07/garbage-collection-in-go-part3-gcpacing.html)介绍了pace算法如何根据业务类型来识别出最佳的pace。

首先，这个文章介绍了如何打印trace，（这个是什么trace，我之前怎么没见过）
```go
 import "runtime/trace"

 func main() {
     trace.Start(os.Stdout)
     defer trace.Stop()
 }
```
带有trace的代码编译之后用下面命令执行，主要是输入到Stdout的输出重定向到t.out文件中：
```s
$ go build
$ ./trace > t.out
```
输出文件之后，运行下面命令，这个文件不能直接打开，一些类似乱码的东西：
```s
go tool trace t.out
```
控制台会有下面输出，我们可以使用chrome打开下面的连接，然后就有下面的界面，有空可以详细研究一下这个trace：
```s
root@z-Latitude:trace# go tool trace a.out 
2020/03/26 18:48:50 Parsing trace...
2020/03/26 18:48:50 Serializing trace...
2020/03/26 18:48:50 Splitting trace...
2020/03/26 18:48:50 Opening browser
Trace viewer is listening on http://127.0.0.1:45761
```
![java-javascript](/img/in-post/go-gc-lab/Picture2.png)
在[GC Pacing](https://www.ardanlabs.com/blog/2019/07/garbage-collection-in-go-part3-gcpacing.html)这篇文章中，对同一个任务写了三个程序，第一个是顺序扫描所有的文件，第二个是每个文件起一个goroutine，第三个使用goroutine池，数量为逻辑CPU个数，用池中的goroutine来扫描文件，配合使用Go trace，三类程序的GC时间为：
```s
| Algorithm  | Program | GC Time  | % Of GC | # of GC’s | Avg GC   | Max Heap |
|------------|---------|----------|---------|-----------|----------|----------|
| freq           | 2626 ms |  64.5 ms |     ~2% |       232 |   278 μs |    4 meg |
| concurrent |  951 ms | 284.4 ms |    ~34% |        23 |  12.3 ms |  200 meg |
| numCPU     |  754 ms | 177.7 ms |    ~25% |       467 | 380.5 μs |    4 meg |
```
结论很笼统，自己体会吧

#### GC带来的影响
一共有两类影响：
* GC会占用逻辑CPU P，因此在收集阶段，goroutine不能使用所有的P。另外，还有一部分P，可能会被征用来执行`Mark Assist`。
* 另一部分影响是STW，在STW期间，所有的应用goroutine都是不能运行的。

#### 程序员能做点什么来减小GC带来的影响
程序员能做的是：识别并减少不必要的内存申请，具体有下面方法（说的比较抽象）：
* 尽量使heap最小（减少内存分配次数以及频率）
* Find an optimal consistent pace.
* Stay within the goal for every collection.
* Minimize the duration of every collection, STW and Mark Assist.


#### 参考文献：

[Ardan labs大作 Garbage Collection In Go : Part I - Semantics](https://www.ardanlabs.com/blog/2018/12/garbage-collection-in-go-part1-semantics.html)

[Garbage Collection In Go : Part II - GC Traces](https://www.ardanlabs.com/blog/2019/05/garbage-collection-in-go-part2-gctraces.html)

[Garbage Collection In Go : Part III - GC Pacing](https://www.ardanlabs.com/blog/2019/07/garbage-collection-in-go-part3-gcpacing.html)

[关于Go1.14，你一定想知道的性能提升与新特性](https://juejin.im/post/5e3f9990e51d4526cc3b1672)

[draveness.me 垃圾收集器](https://draveness.me/golang/docs/part3-runtime/ch07-memory/golang-garbage-collector/)

[Go GC 20 问](https://mp.weixin.qq.com/s/o2oMMh0PF5ZSoYD0XOBY2Q)

[Golang 垃圾回收剖析](http://legendtkl.com/2017/04/28/golang-gc/)
[关于Go1.14，你一定想知道的性能提升与新特性](https://juejin.im/post/5e3f9990e51d4526cc3b1672)
