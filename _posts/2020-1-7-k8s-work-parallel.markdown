---
layout:     post
title:      "K8s 中的 worker queue 并发控制模型"
subtitle:   " \"K8s 中的设计模式\""
date:       2020-1-6 12:13:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 设计模式
---

K8s 默认调度器 genericscheduler 中判断每个 node 是否符合调度条件是并行执行的，具体是通过下面函数：
```go
workqueue.Parallelize(16, len(nodes), checkNode)
```
该函数第一个参数是并行粒度，最多起的 goroutine 的数目；第二个参数是所有节点的个数（理解为任务数），第三个参数是对每个 node 执行的函数，即具体的任务，我们介绍通用设计模式的实现，忽略 checkNode 的具体实现，理解成一个函数就可以了。我们通过这个函数理解下 Golang 中的并发控制。

#### workqueue.Parallelize 设计模式
`Parallelize` 函数定义比较简单，三两句就构成一个并行框架。在该模式中，有两个主体需要注意，一个是 job，另一个是 worker。在 K8s Scheduler 背景下，每一个节点的检查工作就是一个 job，每一个可用的 goroutine 就是 worker，worker 处理 job，并且一个 worker 在同一时间下，只能处理一个 job。

worker 理解成流水线好一点，一共开了固定数量的流水线，流水线处理完一个 job 后就处理下一个，处理完所有 job 后就停工了。对应到 Golang 的设计中，所有的任务都放到一个阻塞队列中，供所有流水线来消费，也就是所有的任务都放到 channel 中，每个 goroutine 都是一个 for 循环，不停的从 channel 中取 job，处理 job。在初始状态下要做两件事：1）保存所有 job 的 channel 要就绪（填充完所有的 job）；2）流水线要开起来，启动固定数量的 goroutine。模式示意图如下。

![java-javascript](/pics/work-queue-ink8s.jpg){:height="50%" width="50%"}

代码实现框架如下，为了描述更通用的设计模式，我把 Scheduler 相关业务代码删除了，只留下了大体框架。
```go
type DoWorkPieceFunc func(piece int)

func Parallelize(workers, pieces int, doWorkPiece DoWorkPieceFunc) {
	toProcess := make(chan int, pieces) // 首先创建一个缓冲区大小为 pieces，并填充所有的 job
	for i := 0; i < pieces; i++ {
		toProcess <- i
	}
	close(toProcess)    // 填充完就 close，表示：我就这些任务，你们处理完就可以下班了

	wg := sync.WaitGroup{}
	wg.Add(workers)
	for i := 0; i < workers; i++ {  // 启动固定数量的 goroutine，数量即并发度
		go func() {
 			defer wg.Done()
			// 每个 goroutine 只做两件事：
			// 1. 取 job，通过 for range 来取，因为 channel 已经关闭，所有元素取完后，for range 就退出了
			// 2. 处理 job
			for piece := range toProcess {
				doWorkPiece(piece)
			}
		}()
	}
	wg.Wait()    // 等待所有goroutine结束
}
```
在上面代码中，因为填充完 job channel 之后，就把 channel close 关闭了，channel 中没有元素之后，for range 循环就会退出， for range 退出之后，这个 worker 就退出了。代码中还使用了 WaitGroup 来等待所有的 worker 退出。

#### 另外一个问题，控制并发数
有时候，我们需要执行很多任务，成百上千个，每个任务都由一个 goroutine 来处理，任务的创建也都是按需的（并且有可能是第三方发起的 goroutine，我们无法控制 goroutine 数量）。如果同时运行的 goroutine 太多，可能负载就太高了，我们希望同一时刻只能运行固定数量的 goroutine，这个有点类似于流控的概念。

无法控制 goroutine 数量，但是想控制同时运行的 goroutine 数量（其他未运行的 goroutine 在排队），这个也可以通过 channel 来实现，刚刚说了有点类似于流控，其实现也跟流控是一样的，即每个 goroutine 在运行之前试图取一个 token，能取到就执行，取不到就阻塞，直到取到。可以通过下面的代码框架来说明。
```go
func takeToken(tokens chan struct{}) {
    // 往 channel 中写数据，刚开始时 channel 为空，是能写进去的
    tokens <- struct{}{}
}
func releaseToken(tokens chan struct{}) {
    <- tokens
}

// 并发度控制为 10，一共有 10 个可用的 token
var tokens = make(chan struct{}, 10)

// 对于每个要启动 goroutine 执行 job 的 worker，在 goroutine 里要取到 token 才能执行 
for i := ranges workers {
    go func(w worker){
        takeToken(tokens)
        startWorker(w)
        releaseToken(tokens)
    }(i)
}
// 结束
```