---
layout:     post
title:      "《In search of an Understandable Consensus Algorithm》Etcd 论文简析"
date:       2021-12-11 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Etcd
---

今天去参加前同事聚会了，大佬们都很厉害，我什么时候才能成为大佬。
## 前言
理解一下 etcd 论文，目的在理解其核心思想。在阅读 etcd 源码时，能够分清哪些东西是为了实现协议额外添加的，哪些是 etcd 自身的东西。同时，这些东西比较零碎，记在这里，能够随时回来翻一下。对于论文中不明确的内容，将以英文的形式直接列出来，避免引起误导。

etcd 论文中提供的方法在实现时可能略有差异，有些细节不必太在意，只记录大体思路。

## 5. The Raft consensus algorithm
Raft 协议是基于 leader 的（集群中有一个 leader），在基于 leader 的实现中， etcd 将一致性分为三个核心问题：
* Leader 选举：关注 leader 挂了的时候如何选出一个 leader
* Log replication：leader 必须接收 client 发来的 entry，并将 entry 在集群中复制，转发到其他节点。
* Safety：the key safety property for Raft is the State Machine Safety Property in Figure 3: if any server has applied a patricular log entry to its state machine, then no other server may apply a different command for the same log index。Section 5.4 describes how Raft ensures this property; the solution involves an additional restriction described in Section 5.2.    

第五章是介绍 etcd 算法的核心，包括：
1. 5.1 Raft basics
2. 5.2 Leader election
3. 5.3 Log replication
4. 5.4 Safety，通过一些额外措施，来保证定理的正确性

论文通过几个框图来介绍了 Raft 的思想。我们看下这几个框图。

### 节点状态 state
![java-javascript](/img/in-post/all-in-one/2021-12-12-13-12-47.png)
节点的状态，按照是持久化在磁盘中，还是保存在内存中，分为 Persistent 以及 volatile，同时 leader 中还有部分额外的信息，具体分下面几部分。
#### 所有节点上都持久化的状态（Persistent）
(Updated on stable storage before responding to RPCs，RPCs 请求返回之前先持久化到本地)
* currentTerm：这个节点看到的当前任期号，（任期号这东西，在初始化是被置为0，后面开始单调递增）。每次 candidate 发起选举时都会自增 term，就算选举不成功（指此轮选举，没有 leader 产生），
* votedFor: 在当前任期中，给谁投过票，（没投过票这个就是null）
* log[]: 就是 raft log，log entries，每个 entry 包含：1）持久化状态机需要执行的命令。2）每个 entry 的任期号，leader 在收到这个 entry 时的 term。其中持久化状态机不属于 raft 理论，raft 只负责维持 raft log。3）entry 的 index，entry 的编号，这个也是单调递增的。

#### 所有节点都是 volatile 的状态
* commitIndex: 已经提交的 entry 的最大的索引，初始化为 0，也是单调递增的。
* lastApplied: 已经 Apply 到状态机的最大的 entry 的索引。

#### leader 节点中 volatile 的状态
这些状态咋重新选举后会被初始化。
* nextIndex[]: 对于每个其他节点（也就是其他 followers），需要发送的 index 值，（initialized to leader last log index + 1）
* matchIndex[]: 对齐其他每个 followers，已知的已经复制过去的最高的 index 值。（初始化为 0，单调递增）

### AppendEntries RPC
![java-javascript](/img/in-post/all-in-one/2021-12-12-18-39-32.png)
leader 通过 AppendEntries RPC 请求来复制日志到 follower 节点，心跳也是通过 AppendEntries RPC 请求来实现的。

#### 参数
* term: leader 的任期号
* leaderId: so follower can redirect clients (follower 可以根据此 ID 来转发一致性请求到 leader)，（另外，每个 follower 都还有一个状态来存储当前集群中的 leader）
* prevLogIndex: index of log entry immediately preceding new ones。最新的 entry 之前的 index。(用于快速失败)
* prevLogTerm: term of prevLogIndex entry
* entries[]: 需要持久化的日志，如果是心跳则为空，可能包含多个 entry。
* leaderCommit: leader 的 commitIndex。

#### 返回值
* term: currentTerm, for leader to update itself
* success: true if follower contained entry matching prevLogIndex and prevLogTerm
#### follower 对 AppendEntries RPC 的处理
1. 如果当前的 term 大于 AppendEntries 中的 term 则返回 false
2. 如果 follower 位于 prevLogIndex 处的 entry 中的 term 跟 prevLogTerm 不一致，则返回 false.
3. 如果 2 条件满足，并且本地存在一个 entry 跟请求传过来的 entry 冲突，则删除本地的 entry，包括冲突位置的 entry 以及后面的 entry。冲突是指：index 相同，但是 term 不同。
4. 将本地不存在的 entry 追加到本地。
5. 如果请求传递过来的 leaderCommit 大于本地的 commitIndex，将 commitIndex 设置为：min(leaderCommit, index of last new entry)

### RequestVote RPC
Candidates 在请求投票时，会发送此请求。这个先不分析。
![java-javascript](/img/in-post/all-in-one/2021-12-12-19-04-54.png)

### Raft 节点的行为（Rules for servers）
![java-javascript](/img/in-post/all-in-one/2021-12-12-19-12-47.png)

#### 所有节点
* 如果 commitIndex 大于 lastApplied，则增大 lastApplied，并且将日志应用到状态机中。
* 如果 RPC 请求中包含的 term 大于当前的 term，则修改当前的 term，并且自身转换为 follower.

#### followers
* 处理来自 candidate 以及 leader 的请求
* 如果超过选举超时时间，且没有收到 AppendEntries 请求，则转换为 Candidate，并发起投票。

#### Candidates
* 转换为 candidates 之后，开启选举
    * 提高 currentTerm
    * 投自己一票
    * 重置选举超时时间
    * 向其他所有节点发送请求投票请求，即 RequestVote RPC
* 如果得到大多数投票，转化为 leader.
* 如果收到了来自 leader 的 AppendEntries RPC，则转换为 follower。
* 如果在 election timeout 之内没能当选为 leader，重新发起一次选举。

#### Leader
* 一旦选举成功，则向其他所有节点发送 AppendEntries RPC 请求（空的请求，即心跳）
* 如果收到了来自 client 的 command 请求，将 entry 添加到本地 log，respond after entry applied to state machine（这个不太确定，是持久化状态机后返回吗？）
* 如果 last log index 大于一个 follower 的 nextIndex，对这个 follower 发送 AppendEntries 请求。
    * 如果 AppendEntries 发送成功，更新这个 follower 的 nextIndex 以及 matchIndex。
    * 如果因为 log 不一致， AppendEntries 请求失败了，降低 nextIndex，重新发送 Append 请求。
* 更新 commitIndex，更新规则如下：如果存在一个 N，N > commitIndex，并且大多数 follower 的 matchIndex 都大于 N，并且 entry N 的 term 等于 currentTerm，则更新 commitI

### 定理
![java-javascript](/img/in-post/all-in-one/2021-12-12-21-39-22.png)
raft 提出了一些定理，这些理论在系统运行的任何时间都是成立的。同时 raft 为了保证这些定理的成立，为算法的实现添加了一些约束。
* Election Safety: 在任何的 term 中，至多有一个 leader.
* Leader Append-Only: leader 从不删除或者覆盖自己的 log，只会追加。$5.3
* Log Matching: if two logs contain an entry with the same index and term, then the logs are identical in all entries up through the given index. $5.3
* Leader Completeness: if a log entry is committed in a given term, then that entry will be present in the logs of the leaders for all higher-numbered terms. $5.4
* StateMachineSafety: ifaserverhasappliedalogentryat a given index to its state machine, no other server will ever apply a different log entry for the same index. $5.4.3

一次整理完有点难，有时间再补充下