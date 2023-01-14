---
layout:     post
title:      "K8s worker queue 设计模式"
subtitle:   " \"K8s 中的设计模式\""
date:       2020-1-6 12:13:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 设计模式
---

K8s generic scheduler 中判断每个 node 是否符合条件是并行执行的，具体是通过下面语句：

`workqueue.Parallelize(16, len(nodes), checkNode)`

第一个参数是并行粒度，最多起的goroutine的数目，第二个参数是所有节点的个数，第三个参数是对每个node执行的函数。

#### 关于checkNode函数
这个函数需要一个参数`i`，表示当前节点在节点数组中的编号，也就是判断第i个节点是否符合要求，其定义如下。因为checkNode是并行访问的，所以写`errs`数组以及`failedPredicateMap`map的时候都需要加锁，
读`nodes`数组是不需要加锁的。
```go
checkNode := func(i int) {
    nodeName := nodes[i].Name
    fits, failedPredicates, err := podFitsOnNode(pod, meta, nodeNameToInfo[nodeName], predicateFuncs, ecache, schedulingQueue, alwaysCheckAllPredicates)
    if err != nil {
        predicateResultLock.Lock()
        errs[err.Error()]++
        predicateResultLock.Unlock()
        return
    }
    if fits {
        filtered[atomic.AddInt32(&filteredLen, 1)-1] = nodes[i]
    } else {
        predicateResultLock.Lock()
        failedPredicateMap[nodeName] = failedPredicates
        predicateResultLock.Unlock()
    }
}
```
`podFitsOnNode`就是遍历所有的predicate，看看这个node符合不符合条件，对于同一个node来说，所有predicate是**顺序**执行的，在`alwaysCheckAllPredicates`设置为false(默认)情况下，遇到一个predicate不通过，podFitsOnNode函数就返回了，俗称短路。

#### 关于workqueue.Parallelize
这个函数定义比较简单，三两句就构成一个并行框架，首先是创建一个待缓冲区的channel，缓冲区大小等于所有的待处理任务数量，然后启动一堆goroutine，goroutine的个数就是并行粒度，
也就是`workers`的数量，每个goroutine都在竞争channel中的资源，竞争到了就执行任务，如果channel中的任务处理完了，就退出竞争，并结束goroutine，所有任务都处理完时，所有goroutine也就都退出了。在一般情况下，workers数量是小于piece数量的，所以第一轮竞争，每个goroutine都能拿到一个任务，下一轮竞争时，谁结束的早，谁就先拿到任务。
```go
type DoWorkPieceFunc func(piece int)

// Parallelize is a very simple framework that allow for parallelizing
// N independent pieces of work.
func Parallelize(workers, pieces int, doWorkPiece DoWorkPieceFunc) {
    // 首先创建一个缓冲区大小为pieces，类型为int的通道
	toProcess := make(chan int, pieces)
	for i := 0; i < pieces; i++ {
		toProcess <- i
	}
    // 在channel中的数据都读取完之后关闭通道，close是非阻塞的
	close(toProcess)

	if pieces < workers {
		workers = pieces
	}

	wg := sync.WaitGroup{}
	wg.Add(workers)
    // 启动workers个goroutine
	for i := 0; i < workers; i++ {
		go func() {
			defer utilruntime.HandleCrash()
            defer wg.Done()
            // 每个goroutine都试图从channel中获取任务
			for piece := range toProcess {
				doWorkPiece(piece)
			}
		}()
	}
    // 等待所有goroutine结束
	wg.Wait()
}
```
在上面代码中，goroutine是不会阻塞的，任务处理完了就退出。然而如果不调用`close(toProcess)`，情况就不一样了，在不调用close的情况下，所有的goroutine在拿不到任务时都会阻塞，整个Parallelize函数也永远不会退出。