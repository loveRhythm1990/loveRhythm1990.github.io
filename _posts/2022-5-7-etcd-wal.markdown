---
layout:     post
title:      "Etcd 中的 wal 处理流程"
date:       2022-5-7 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - Etcd
---

在 Etcd 数据目录下，有一些类似如下的 wal 日志文件，本文尝试解释下 wal 文件的产生和其工作原理。在下面显示的文件中，其命名格式为`%016x-%016x.wal`，由 `-` 分割的前者为 seq，是一个从 0 开始的序号，每新增一个 wal 文件，这个序号就加 1，从下面也能看出这个序号是递增的，`-` 后面为该 wal 保存的 Entry 的最大的 index，从重放 wal 的时候需要用到这个 index。
```s
[root@centos etcd]# ls -lh member/wal/
总用量 428M
-rw-r--r-- 1 root root 62M 9月  11 2021 0000000000001693-000000000e1e1702.wal.broken
-rw------- 1 root root 62M 5月  23 21:50 000000000000b0ff-000000004f371b30.wal
-rw------- 1 root root 62M 5月  23 21:56 000000000000b100-000000004f3799aa.wal
-rw------- 1 root root 62M 5月  23 22:03 000000000000b101-000000004f381357.wal
-rw------- 1 root root 62M 5月  23 22:09 000000000000b102-000000004f389243.wal
-rw------- 1 root root 62M 5月  23 22:13 000000000000b103-000000004f390bd6.wal
-rw------- 1 root root 62M 5月  23 22:09 0.tmp
```

### EtcdServer 初始化对 wal 的处理
代码在初始化 wal，一个比较重要的标识是 `haveWAL`，这个标志用于说明在 `member/wal/` 目录是否存在后缀为 `.wal` 的文件，并根据这个标志以及当前集群是否是新集群，划分了三种情况：1）此节点为往已有集群中新增的一个节点：调用 startNode 初始化节点；2）新启动了一个 Etcd 集群：调用 startNode 初始化节点；3）已有 Etcd 集群中的节点重启了：调用 restartNode 初始化节点。

在 startNode 中是通过调用 `wal.Create` 初始化 wal，在 restartNode 中，是通过调用 `wal.Open` 初始 wal。
```go
func NewServer(cfg ServerConfig) (srv *EtcdServer, err error) {
	haveWAL := wal.Exist(cfg.WALDir())
	switch {
	case !haveWAL && !cfg.NewCluster: // 没有 wal 目录，且不是新集群，说明是往集群中添加一个新的 Etcd 节点
		// ...
		id, n, s, w = startNode(cfg, cl, nil) // 调用 startNode
	case !haveWAL && cfg.NewCluster:  // 新启动一个集群
		// ... 
		id, n, s, w = startNode(cfg, cl, cl.MemberIDs())  // 调用 startNode
	case haveWAL: // 事先存在一个 wal 目录，说明是 Etcd 节点重启
		if !cfg.ForceNewCluster { 
      id, cl, n, s, w = restartNode(cfg, snapshot)  // 通过 restartNode 来初始化 wal
		} else {  // forceNewCluster 是指将当前节点重置为单节点 etcd 集群
			id, cl, n, s, w = restartAsStandaloneNode(cfg, snapshot)
		}
	default:
	}
	// ...
	return srv, nil
}
```
`wal.Create` 所在的 代码文件为 `etcd/wal/wal.go`，`wal.Open` 也在这个文件中，简单分析一下这两个函数做的事情。

#### startNode 通过 wal.Create 新建 wal 文件
`wal.Create` 的代码就不贴了，可以直接在文件 `etcd/wal/wal.go` 看，其函数签名如下，函数最后一个参数为 metadata，其内容为 clusterID 以及 nodeID。
```go
func Create(lg *zap.Logger, dirpath string, metadata []byte) (*WAL, error)
```
概括来说，`wal.Create` 做了下面事情：
1. 新建一个临时**目录** `wal.tmp`，在目录中建第一个日志文件，并预先分配 64M 空间。
2. 初始化编码器 encoder，该 encoder 在写 `walpb.Record` 到文件前，会先生成一个 crc 校验码。
3. 日志文件准备好之后，rename 回 `etcd/wal`，这样使文件的准备工作看起来是原子的。rename 之后还要调用 Fsync 刷新文件元数据。
4. ... 

#### restartNode 通过 wal.Open 读取比快照新的那部分 wal
在 Etcd 节点只是重启的情况下，wal 文件是事先存在的，这个时候从 snap 和 wal 恢复 Etcd 节点。 snap 的目录为 `member/snap/`，恢复 snap 时，实际是读一个时间顺序最新的 snap，然后反序列化为结构体 `store`(定义的文件为`etcd/etcdserver/api/v2store/store.go`)， snap 文件存放的实际就是这个结构体的 json 序列化。在重放 wal 时，也要用到上面的 snap，其实只用到两个字段 `Index` 和 `Trem`，表示节点重启前 Entry 的索引和 leader 任期。

在 restartNode 方法中，首先调用的方法是 readWAL 方法，在 readWAL 方法中调用了 wal.Open 以及 WAL.ReadAll 方法，其函数声明如下，下面简单梳理下这几个函数的作用。
```go
func readWAL(lg *zap.Logger, waldir string, snap walpb.Snapshot) (w *wal.WAL, id, cid types.ID, st raftpb.HardState, ents []raftpb.Entry)

func Open(lg *zap.Logger, dirpath string, snap walpb.Snapshot) (*WAL, error)

func (w *WAL) ReadAll() (metadata []byte, state raftpb.HardState, ents []raftpb.Entry, err error)
```
在初始化 EtcdServer 时，会先找一个最新的 snap，并用这个 snap 恢复 store 结构体，然后这个 snap 中的 index 来重放 wal 文件。
* Open：Open 函数的作用是先遍历所有的 wal 文件，在这些 wal 的末尾 index 中找一个小于等于 snap.index 最大值，然后返回一个，所有比 snap.index 大的（包括那个小于等于）的 wal 文件列表，这个文件列表最终被封装在 WAL 结构体中，用来新建一个 WAL。Etcd 中的 snap（`member/snap/` 目录下的文件） 实际存储的是下面的结构体序列化数据，定义在文件 `etcd/etcdserver/api/v2store/store.go` 中。
```go
type store struct {
	Root           *node
	WatcherHub     *watcherHub
	CurrentIndex   uint64
	Stats          *Stats
	CurrentVersion int
	ttlKeyHeap     *ttlKeyHeap  // need to recovery manually
	worldLock      sync.RWMutex // stop the world lock
	clock          clockwork.Clock
	readonlySet    types.Set
}
```
* ReadAll：读取上面 Open 函数返回的所有 wal.Record，根据 Record，输出其中的 metadata、entry 等，wal.Record 一共有 5 种类型，定义在 `wal/wal.go` 中。
```go
const (
	metadataType int64 = iota + 1
	entryType
	stateType
	crcType
	snapshotType
)
```
* readWal：就是顺序调用上面两个方法。

restartNode 在通过 `readWal` 读 wal 日志之后，返回其中保存的一系列数据，包括 entry/metadata/hardstate 等，在返回这些数据之后，将这些数据保存到 raftLog 存储中，raftLog 中也有一个存储接口 MemoryStorage，这个接口我们这里不多介绍。

上面介绍的是在 Etcd 启动一个节点的时候，WAL 是如何初始化的，在 WAL 初始化完成之后，会将初始化后的 WAL 以及 snapshot 组成一个 Storage 对象，保存到 EtcdServer 结构体中。这个存储接口主要是持久化存储，包括 wal 以及快照等（这个跟 boltdb 后端存储也要区分开）。上面的 raftLog 是内存存储，用来保存收到的 Entry 等。
```go
srv = &EtcdServer{
	// ...
	r: *newRaftNode(
		raftNodeConfig{
			lg:          cfg.Logger,
			isIDRemoved: func(id uint64) bool { return cl.IsIDRemoved(types.ID(id)) },
			Node:        n,
			heartbeat:   heartbeat,
			raftStorage: s,
			// 这里的 w 就是 WAL 结构体
			storage:     NewStorage(w, ss),
		},
	),
}
```
其中 Storage 接口的定义如下，在文件 `etcd/etcdserver/storage.go` 中。
```go
type Storage interface {
	// 将 ents 以及 state 保存到持久化存储，这里是 wal，是阻塞的，成功才返回
	Save(st raftpb.HardState, ents []raftpb.Entry) error
	// 这里的 SaveSnap 是保存数据到 member/snap 目录
	SaveSnap(snap raftpb.Snapshot) error
	Close() error
}
type storage struct {
	*wal.WAL
	*snap.Snapshotter
}
```
### EtcdServer 消费 Ready 写 wal
写 wal 的时机就是上述 Storage 接口中 Save 方法的调用时机，为了理解正常请求下的 wal 流程，这里先不考虑灾备情况下的 wal 恢复。在代码中，上面 Save 方法只有一个地方会被调用，那就是 EtcdServer 在处理 Ready 结构体时（消费 raft 层传递过来的 Ready），下面代码概述了应用层在收到 Ready 之后做的事情。
```go
func (r *raftNode) start(rh *raftReadyHandler) {
	// ... 
	go func() {
		for {
			select {
			case rd := <-r.Ready():
				notifyc := make(chan struct{}, 1)
				ap := apply{
					entries:  rd.CommittedEntries,
					snapshot: rd.Snapshot,
					notifyc:  notifyc,
				}

				if islead { // 对于 leader 发送消息和持久化到 wal 可以同时执行
					r.transport.Send(r.processMessages(rd.Messages))
				}
				// 这里将 Ready 传递过来的 Entry 进行持久化，写到 wal
  				if err := r.storage.Save(rd.HardState, rd.Entries); err != nil {
				}
				// 添加到 memoryStorage
				r.raftStorage.Append(rd.Entries)
				if !islead {
					// 通过网络发消息
					msgs := r.processMessages(rd.Messages)
					r.transport.Send(msgs)
				} else {
					notifyc <- struct{}{}
				}
				r.Advance()
			}
		}
	}()
}
```
在《[Etcd put 请求过程：EtcdServer 处理概述](https://loverhythm1990.github.io/2022/03/02/etcd-put-etcdserver/)》中，我们提到过，对于 leader 来说，收到 Propose 请求后，在 raft 层做两件事：1）将 Entry 追加到 unstable storage 中；2）封装一个针对所有 follower 的 boardcast 请求。Entry 在追加到 unstable storage 之后，最终会通过 Ready 数据结构返回给应用层。所以上面代码中，在调用 `r.storage.Save(rd.HardState, rd.Entries)` 写 wal 时，实际上是将 unstable 中的 Entry 写到了 wal 中。写完 wal 之后，然后就添加到了 MemoryStorage 中（`r.raftStorage.Append(rd.Entries)`），这里的 MemoryStorage 可以看做持久存储，因为写之前已经写了 wal，这个 MemoryStoage 作为 raft 中 entry 的存储系统。
```go
func newReady(r *raft, prevSoftSt *SoftState, prevHardSt pb.HardState) Ready {
	rd := Ready{
		// 在构造 Ready 时，其中的 Entry 就来自 unstable storage
		Entries:          r.raftLog.unstableEntries(),
		CommittedEntries: r.raftLog.nextEnts(),
		Messages:         r.msgs,
	}
	// ...
	return rd
}
```

综上，再回顾下处理一条 Entry 的大致流程。
1. 客户端向 etcd 集群发送一次请求之后，请求中的 Entry 先交给 etcd-raft 模块进行处理，其中 etcd raft 模块会先将 Entry 保存到 unstable 存储中。
2. raft 模块将 Entry 封装到 Ready 结构体中，返回应用层进行持久化。
3. 应用层收到 Ready 中的 Entry 之后，保存到 wal 中，保存到 wal 之后，保存到 memoryStorage 中。
4. 待 Entry 记录被复制到集群中的半数节点之后，该 Entry 会被节点确认为已提交(committed)，并封装进 Ready 实例返回给应用层。
5. 应用层可将 Ready 中待 apply 的 Entry 应用到状态机中。


### 附录
下面是一个 etcd 节点的数据存储目录。
```s
[root@centos etcd]# ls
lost+found  member
[root@centos etcd]# ls member/
snap  wal
[root@centos etcd]# ls -lh member/snap/
总用量 268M
-rw-r--r-- 1 root root 244K 5月  23 20:35 00000000000006cc-000000004f3232a9.snap
-rw-r--r-- 1 root root 244K 5月  23 20:56 00000000000006cc-000000004f33b94a.snap
-rw-r--r-- 1 root root 244K 5月  23 21:17 00000000000006cc-000000004f353feb.snap
-rw-r--r-- 1 root root 244K 5月  23 21:38 00000000000006cc-000000004f36c68c.snap
-rw-r--r-- 1 root root 244K 5月  23 21:59 00000000000006cc-000000004f384d2d.snap
-rw------- 1 root root 267M 5月  23 22:13 db
[root@centos etcd]# ls -lh member/wal/
总用量 428M
-rw-r--r-- 1 root root 62M 9月  11 2021 0000000000001693-000000000e1e1702.wal.broken
-rw------- 1 root root 62M 5月  23 21:50 000000000000b0ff-000000004f371b30.wal
-rw------- 1 root root 62M 5月  23 21:56 000000000000b100-000000004f3799aa.wal
-rw------- 1 root root 62M 5月  23 22:03 000000000000b101-000000004f381357.wal
-rw------- 1 root root 62M 5月  23 22:09 000000000000b102-000000004f389243.wal
-rw------- 1 root root 62M 5月  23 22:13 000000000000b103-000000004f390bd6.wal
-rw------- 1 root root 62M 5月  23 22:09 0.tmp
```

### 参考
《[etcd存储格式分析：snap与wal](https://mp.weixin.qq.com/s?__biz=MzU2MjM1NTY3Mw==&mid=2247484387&idx=1&sn=a74c76e8058b7bfc81f850f0c4d26df7&chksm=fc6b8f55cb1c0643b17314e03a512ef46c5d4162ee0de1dd5371d8fd402552c9ce4b887de31b&cur_album_id=1834486169913327616&scene=190#rd)》
