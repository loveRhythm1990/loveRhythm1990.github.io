---
layout:     post
title:      "K8s 调度器中 findNodesThatFit 实现"
subtitle:   " \"scheduler中的细节\""
date:       2019-09-17 19:48:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
    - Scheduler
---

> 由点到线，由线到面。本文针对的k8s的版本是1.16.0

`findNodesThatFit`是scheduler调用predicate的实现，即对每个node调用一系列predicate方法，查看这个node是否满足这个predicate，并返回一个满足这些predicate的node的列表，后续会对这个列表执行priority操作。
博客最后有这个函数的完整代码。

### podFitsOnNode
检查node是否满足条件的具体方法，返回这个节点是否满足条件，如果不满足，则返回原因。另外`podFitsOnNode`有一个参数`AlwaysCheckAllPredicates`用来控制是否短路predicate，短路是指，如果一个node执行一个predicate失败了，后面的就不执行了。
podFitsOnNode调用一系列predicates是有序的，目前是安装静态顺序，如下所示：
```golang
	for _, predicateKey := range predicates.Ordering() {
```

### ParallelizeUntil
`ParallelizeUntil`是一个并行任务框架，可以让一个队列里的特定数目（假设为k）的任务并行运行，思路就是启动k个goroutine去读（利用for range循环读，能拿到任务就处理）一个channel。
主要代码是下面一行，`checkNode`方法是对一个node调用所有的predicate（具体是调用`podFitsOnNode`方法）。

`workqueue.ParallelizeUntil(ctx, 16, int(allNodes), checkNode)`

```go
// ParallelizeUntil is a framework that allows for parallelizing N
// independent pieces of work until done or the context is canceled.
func ParallelizeUntil(ctx context.Context, workers, pieces int, doWorkPiece DoWorkPieceFunc) {
	// stop是一个只读的channel，表示传进来的ctx的stop channel。
	var stop <-chan struct{}
	if ctx != nil {
		stop = ctx.Done()
	}

    // toProcess是存放所有任务编号的队列
	toProcess := make(chan int, pieces)
	for i := 0; i < pieces; i++ {
		toProcess <- i
	}
	// 把所有任务编号放到channel之后，关闭channel
	close(toProcess)

	if pieces < workers {
		workers = pieces
	}

	// wg用来等待worker全部结束，下面for循环，启动workers个数量的goroutine，每个goroutine都是一个
	// for range不断从队列（channel）里读取任务（读一个任务的id，然后把这个id传递给处理函数`doWorkPiece`）。
	wg := sync.WaitGroup{}
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer utilruntime.HandleCrash()
			defer wg.Done()
			for piece := range toProcess {
				// 一旦stop channel关闭就返回
				select {
				case <-stop:
					return
				default:
					doWorkPiece(piece)
				}
			}
		}()
	}
	wg.Wait()
}
```
### context
`findNodesThatFit`中有一行代码创建了一个context，用来结束worker goroutine，结束goroutine的条件是已经找到了足够的node，不需要再查找其他node了，关于足够的node，有两个默认参数：`minFeasibleNodesToFind`,`DefaultPercentageOfNodesToScore`，默认值分别为100，以及50%，分别表示最少的查找的node，以及一个查找的可行的node占所有node的百分比。
```golang
	ctx, cancel := context.WithCancel(context.Background())
```
如果已经找到了足够的node，则调用创建context时返回的cancel函数，关闭通道，从而通知worker停止工作。
```golang
	length := atomic.AddInt32(&filteredLen, 1)
	if length > numNodesToFind {
		cancel()
		atomic.AddInt32(&filteredLen, -1)
	} 
```

此外，generic_scheduler还允许对scheduler进行扩展，用来管理不是由k8s直接管理的资源。
```golang
if len(filtered) > 0 && len(g.extenders) != 0 {

	// 执行extender的过滤操作
	filteredList, failedMap, err := extender.Filter(pod, filtered, g.nodeInfoSnapshot.NodeInfoMap)
	// 忽略其他代码
}
```

完整代码如下：
```go
func (g *genericScheduler) findNodesThatFit(pluginContext *framework.PluginContext, 
pod *v1.Pod, nodeLister algorithm.NodeLister) ([]*v1.Node, FailedPredicateMap, framework.NodeToStatusMap, error) {

	var filtered []*v1.Node
	failedPredicateMap := FailedPredicateMap{}
	filteredNodesStatuses := framework.NodeToStatusMap{}

	if len(g.predicates) == 0 {
		filtered = nodeLister.ListNodes()
	} else {
		allNodes := int32(g.cache.NodeTree().NumNodes())
		numNodesToFind := g.numFeasibleNodesToFind(allNodes)

		// Create filtered list with enough space to avoid growing it
		// and allow assigning.
		filtered = make([]*v1.Node, numNodesToFind)
		errs := errors.MessageCountMap{}
		var (
			predicateResultLock sync.Mutex
			filteredLen         int32
		)

		ctx, cancel := context.WithCancel(context.Background())

		// We can use the same metadata producer for all nodes.
		meta := g.predicateMetaProducer(pod, g.nodeInfoSnapshot.NodeInfoMap)

		checkNode := func(i int) {
			nodeName := g.cache.NodeTree().Next()

			fits, failedPredicates, err := podFitsOnNode(
				pod,
				meta,
				g.nodeInfoSnapshot.NodeInfoMap[nodeName],
				g.predicates,
				g.schedulingQueue,
				g.alwaysCheckAllPredicates,
			)
			if err != nil {
				predicateResultLock.Lock()
				errs[err.Error()]++
				predicateResultLock.Unlock()
				return
			}
			if fits {
				// Iterate each plugin to verify current node
				status := g.framework.RunFilterPlugins(pluginContext, pod, nodeName)
				if !status.IsSuccess() {
					predicateResultLock.Lock()
					filteredNodesStatuses[nodeName] = status
					if status.Code() != framework.Unschedulable {
						errs[status.Message()]++
					}
					predicateResultLock.Unlock()
					return
				}

				length := atomic.AddInt32(&filteredLen, 1)
				if length > numNodesToFind {
					cancel()
					atomic.AddInt32(&filteredLen, -1)
				} else {
					filtered[length-1] = g.nodeInfoSnapshot.NodeInfoMap[nodeName].Node()
				}
			} else {
				predicateResultLock.Lock()
				failedPredicateMap[nodeName] = failedPredicates
				predicateResultLock.Unlock()
			}
		}

		// Stops searching for more nodes once the configured number of feasible nodes
		// are found.
		workqueue.ParallelizeUntil(ctx, 16, int(allNodes), checkNode)

		filtered = filtered[:filteredLen]
		if len(errs) > 0 {
			return []*v1.Node{}, FailedPredicateMap{}, framework.NodeToStatusMap{}, errors.CreateAggregateFromMessageCountMap(errs)
		}
	}

	if len(filtered) > 0 && len(g.extenders) != 0 {
		for _, extender := range g.extenders {
			if !extender.IsInterested(pod) {
				continue
			}
			filteredList, failedMap, err := extender.Filter(pod, filtered, g.nodeInfoSnapshot.NodeInfoMap)
			if err != nil {
				if extender.IsIgnorable() {
					klog.Warningf("Skipping extender %v as it returned error %v and has ignorable flag set",
						extender, err)
					continue
				}

				return []*v1.Node{}, FailedPredicateMap{}, framework.NodeToStatusMap{}, err
			}

			for failedNodeName, failedMsg := range failedMap {
				if _, found := failedPredicateMap[failedNodeName]; !found {
					failedPredicateMap[failedNodeName] = []predicates.PredicateFailureReason{}
				}
				failedPredicateMap[failedNodeName] = append(failedPredicateMap[failedNodeName], predicates.NewFailureReason(failedMsg))
			}
			filtered = filteredList
			if len(filtered) == 0 {
				break
			}
		}
	}
	return filtered, failedPredicateMap, filteredNodesStatuses, nil
}
```