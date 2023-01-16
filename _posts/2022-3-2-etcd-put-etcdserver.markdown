---
layout:     post
title:      "Etcd put 请求过程：EtcdServer 处理概述"
date:       2022-3-2 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Etcd
---

这部分从 EtcdServer 处理 put 请求开始，介绍相关逻辑。这部分内容比较多，涉及到较多模块，这篇文章的主要目的是先有个大题的概念，将各个模块串联起来，同时明确模块之前的接口、通讯方式等，模块内部的细节等有了大概概念之后，再进行分析。

### EtcdServer 发送请求到 Raft
在《[Etcd put 请求过程：clientv3 发送请求到 EtcdServer](https://loverhythm1990.github.io/2021/11/03/etcd-put/)》的分析中，提到了 put 请求最终调用了 EtcdServer 的 Put 方法，这里就从 EtcdServer 的 Put 方法开始看。
```go
func (s *EtcdServer) Put(ctx context.Context, r *pb.PutRequest) (*pb.PutResponse, error) {
	ctx = context.WithValue(ctx, traceutil.StartTimeKey, time.Now())
	resp, err := s.raftRequest(ctx, pb.InternalRaftRequest{Put: r})
	if err != nil {
		return nil, err
	}
	return resp.(*pb.PutResponse), nil
}
```
从 `raftRequest` 跟进去会发现一个 `processInternalRaftRequestOnce` 方法。在此方法中，如果 CommittedIndex 超出 AppliedIndex 太多的话，返回 `TooManyRequest` 错误，此刻其实还没有将请求交付给 raft 模块，仅凭当前节点（收到 put 请求的节点）的两个 index 来进行限速，如果一个节点的磁盘有问题 apply 比较慢，可能就会给客户端返回这个错误。  
```go
func (s *EtcdServer) processInternalRaftRequestOnce(ctx context.Context, r pb.InternalRaftRequest) (*applyResult, error) {
	// 每个 EtcdServer 都有自己的 AppliedIndex 以及 CommitIndex
	ai := s.getAppliedIndex()
	ci := s.getCommittedIndex()
	if ci > ai+maxGapBetweenApplyAndCommitIndex {
		return nil, ErrTooManyRequests	// 返回 too many request 错误，
	}
	r.Header = &pb.RequestHeader{
		ID: s.reqIDGen.Next(),	// 给这个请求随机生成一个 ID，保证是唯一的

	}
	// 省去非主要代码...
	id := r.ID
	ch := s.w.Register(id)	// Etcd 的 Wait 保存了所有事件等待的 channel，
	start := time.Now()
	err = s.r.Propose(cctx, data)	// 向 raft 模块发请求
	if err != nil {
		proposalsFailed.Inc()
		s.w.Trigger(id, nil) // GC wait
		return nil, err
	}
	proposalsPending.Inc()	// 在 proposal 过程中，该监控指标增1 
	defer proposalsPending.Dec()

	select {
	case x := <-ch:	// 阻塞等待整个 proposals 处理过程
		return x.(*applyResult), nil
	case <-cctx.Done():
		proposalsFailed.Inc()
		s.w.Trigger(id, nil) // GC wait
		return nil, s.parseProposeCtxErr(cctx.Err(), start)
	case <-s.done:
		return nil, ErrStopped
	}
}
```
还有地方就是 `ch := s.w.Register(id)`，这个地方是给这个请求生成了一个 channel，并等待这个 channel 被生产者填充，也就是等待处理结果，阻塞式的。wait 的定义可以简单看成 `m map[uint64]chan interface{}`，其中 key 是这个请求的唯一的 ID。

这个方法核心的地方在于 `err = s.r.Propose(cctx, data)`，就是向 raft 模块发请求，向 raft 模块发请求是通过 `Node` interface 来实现的，其定义的文件为：`etcd/raft/node.go`，这里只大概介绍一下，详细内容后面再说
```go
// Node represents a node in a raft cluster.
type Node interface {
	// Propose proposes that data be appended to the log. Note that proposals can be lost without
	// notice, therefore it is user's job to ensure proposal retries.
	Propose(ctx context.Context, data []byte) error
	// ... 省略其他接口
}
```
向 raft 模块发完请求之后，就是阻塞等待请求的结果了，通过上面那个 wait channel。这里有几个监控指标也可以注意一下，比如 `proposalsFailed.Inc()`、`proposalsPending.Inc()` 分别表示失败的 proposal 数量、正在等待的 proposal 数量。

### Raft 处理请求
#### Raft 将请求写入 propc channel
贴一下 Propose 方法，注意一下 Message 的类型 `pb.MsgProp`。并且调用的 `stepWithWaitOption` 方法中，第三个参数 `wait` 是 `true`，表示要阻塞。
```go
func (n *node) Propose(ctx context.Context, data []byte) error {
	return n.stepWait(ctx, pb.Message{Type: pb.MsgProp, Entries: []pb.Entry{{Data: data}}})
}
func (n *node) stepWait(ctx context.Context, m pb.Message) error {
	return n.stepWithWaitOption(ctx, m, true)
}
```
研究下 `stepWithWaitOption` 的实现。在代码的开始，如果不是 `pb.MsgProp` 类型的消息，写入 `n.recvc` 这个 channel 就退出了。对于`pb.MsgProp` 类型，把请求发送到了 `n.propc` 这个 channel，不过也不是直接写请求，写的是 `msgWithResult` 类型，也就是封装了一下，加了一个错误的 channel，其目的还是为了阻塞请求，等待这个 result 有结果之后（err 要么为 nil，要么不为 nil），再返回【这也是一种设计模式，在异步的多线程中，实现同步等待的功能。】。
```go 
type msgWithResult struct {
	m      pb.Message
	result chan error
}
```
`stepWithWaitOption` 的代码如下，其主要作用就是将 `MsgProp` 写入了 `node` 的 `n.propc` 这个 channel，在后面的实现中，我们只要关心 raft 模块是如何从 `n.propc` channel 中取出 Msg 并进行处理的。
```go
func (n *node) stepWithWaitOption(ctx context.Context, m pb.Message, wait bool) error {
	if m.Type != pb.MsgProp {
		select { 
			case n.recvc <- m: return nil
		// ...
		}
	}
	ch := n.propc 
	pm := msgWithResult{m: m}
	if wait {
		pm.result = make(chan error, 1)	// 用来保存处理结果， wait 表示要等待处理完成
	}
	// ...
	select {
	case ch <- pm:	// 将 msgWithResult 写入 propc channel
		if !wait {	return nil }	// 如果不等待，写入 propc channel 就退出			
		// ...
	}
	select {
	case err := <-pm.result:	// 阻塞等待处理结果
		if err != nil { return err }
		// ...
	}
	return nil
}
```
#### Raft 消费 propc channel
上面提到了 `stepWithWaitOption` 方法就是将 Msg 写入了 raft 模块的 `n.propc` 这个 channel。对 propc channel 的消费是在 raft 集群一启动就执行的，有个单独的 goroutine 来执行 node 的 `run` 方法，`go n.run()`，这个方法比较大，目前先关注处理 `propc` channel 的逻辑。
```go
func (n *node) run() {
	// ...
	for {
        // ...
		select {
		case pm := <-propc:	// raft 模块从 channel 中取出 prop 消息进行处理
			m := pm.m
			m.From = r.id
			err := r.Step(m)	// 进入到 Step 方法，处理 message
			if pm.result != nil {	// 处理返回，将结果返回 result channel
				pm.result <- err
				close(pm.result)	// 写入结果后 close result channel，这样之前阻塞的 select 就能解除阻塞了
			}
		// 忽略其他 case
		}
	}
}
```
在对 `n.propc` 的处理中，调用了 `Step` 方法（注意这个 Step 是大写的，因为还有一个小写的 `step` 方法，就是后面的方法）。
对于 Prop 消息来说，可以先略过这个方法，直接看小写的 `step` 方法，小写的 step 方法，根据节点角色的不同，有 `stepFollower`、`stepLeader`、`stepCandidate` 三种实现，我们先关心 `stepFollower`、`stepLeader` 实现。

##### stepFollower
根据上面的分析，`stepFollower` 就是 follower 收到 Prop message 时的处理，在 `stepFollower` 中，枚举了各个消息类型的处理方法，switch 结构还是比较清晰，后面在分析不同类型的消息时，可以直接看这个方法。目前，我们还是只关心 Prop 消息，从代码可以看出，对于 follower 来说，直接调用了 `r.send` 方法。即将消息发送出去。
```go
func stepFollower(r *raft, m pb.Message) error {
	switch m.Type {
	case pb.MsgProp:
		if r.lead == None {
			return ErrProposalDropped
		} else if r.disableProposalForwarding {
			return ErrProposalDropped
		}
		m.To = r.lead	// 指定消息的接收者为 leader
		r.send(m)
	case pb.MsgApp:	// 忽略这些消息类型的处理
	case pb.MsgHeartbeat:
	case pb.MsgSnap:
	case pb.MsgTransferLeader:
	case pb.MsgTimeoutNow:
	case pb.MsgReadIndex:
	case pb.MsgReadIndexResp:
	}
	return nil
}
```
`r.send` 的实现就是想消息 append 到 raft 结构体的 `msgs` 字段，`r.msgs = append(r.msgs, m)`，可见并没有进行实际的消息发送。现在问题来了，我们又得去找 `raft.msgs` 这个字段的消费者。

通过查找字段引用，raft 结构体的字段 `msgs []pb.Message` 只有在 `newReady` 方法中，（根据之前的对 etcd 的理解， `Ready` 结构体是 raft 模块给上层应用传输结果、待发送消息的结构体，也就是 raft 层和应用层的通信通道）。`newReady` 方法就是产生一个新的 `Ready` 结构。

那 `newReady` 是在哪里调用呢？是在 `func (n *node) run() {` 方法中调用的，我们在上面的 `Raft 消费 propc channel` 章节说过，这个方法也是 raft 消费 propc channel 的方法，所以转了一圈又回来了。在 `run()` 方法中，是构造一个 `Ready` 结构（里面包含待发送的 prop 消息）供上层应用消费。

##### stepLeader
现在我们再来看下 leader 怎么处理 prop 消息。看下 `stepLeader` 的实现
```go
func stepLeader(r *raft, m pb.Message) error {
	switch m.Type {
	case pb.MsgBeat:	// 忽略这些 case
	case pb.MsgCheckQuorum:
	case pb.MsgProp:
		if r.prs.Progress[r.id] == nil {
			return ErrProposalDropped	// 返回 “raft proposal dropped” 错误
		}
		if r.leadTransferee != None {
			r.logger.Debugf("%x [term %d] transfer leadership to %x is in progress; dropping proposal", r.id, r.Term, r.leadTransferee)
			return ErrProposalDropped
		}
		// ...
		if !r.appendEntry(m.Entries...) {	// 1. 执行 append Entry
			return ErrProposalDropped
		}
		r.bcastAppend()	// 2. 向 follower 广播 append
		return nil
	case pb.MsgReadIndex: 
		// ...
		return nil
	}
```
这里有两步：1）`appendEntry`，2）`bcastAppend`。前者主要是 Entry 追加到本地的 `unstable` 缓存中。
```go
func (r *raft) appendEntry(es ...pb.Entry) (accepted bool) {
	li := r.raftLog.lastIndex()
	for i := range es {
		es[i].Term = r.Term
		es[i].Index = li + 1 + uint64(i)
	}
	if !r.increaseUncommittedSize(es) {
		// 看下是否超过了最大的没有 committed 的size，默认是 1<<30 字节，大概是 1G.
		r.logger.Debugf("%x appending new entries to log would exceed uncommitted entry size limit; dropping proposal",r.id,)
		// Drop the proposal.
		return false
	}
	li = r.raftLog.append(es...)	// 添加到 raftLog 的 unstable 缓存中
	r.prs.Progress[r.id].MaybeUpdate(li)
	r.maybeCommit()	// 看下能否提高下 Commit Index。
	return true
}
```
`bcastAppend` 主要是为每个 follower 生成一个消息，跟上面 follower 发送 prop 消息给 leader 一样，这里也是只是追加到 raft 结构体的字段 `msgs []pb.Message`，当然要指定消息的接收者，这里消息的接收者为每个 follower，即下面的 `to` 参数。这个消息最终也是通过 `Ready` 结构体传输给应用层的，也就是 EtcdServer。
```go
func (r *raft) sendAppend(to uint64) {
	r.maybeSendAppend(to, true)
}
```
总结一下，Leader 收到 prop 消息之后，做两件事，一个是追加到本地的 unstable 缓存，另一个是给所有的 follower 广播消息。对于 unstable 后面再写一篇文章单独分析。

概括来讲，对于 follower 节点来讲，收到 propose 请求之后，是需要将这个请求转发给 leader 的，其处理过程就是生成一个 Msg，Msg 的目的地是 leader，并将这个 Msg 通过 Ready 结构体传递给上层应用 EtcdServer，EtcdServer 再通过网络层（定制化的 Transport）来发送消息。对于 leader 来讲情况是类似的，消息的发送也是先通过 Ready 传递给应用层。但是我们知道在处理写请求时，还有写 wal 日志、快照等，同时 leader 还要更新 commitIndex 等，这些操作都是在应用层处理 Ready 时做的，具体是在 `func (r *raftNode) start(rh *raftReadyHandler)` 方法中做的，后面也会有一篇文章来介绍这个过程。
