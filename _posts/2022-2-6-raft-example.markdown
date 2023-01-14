---
layout:     post
title:      "Etcd raft 模块应用案例解析：raftexample"
date:       2022-2-6 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Etcd
---

etcd 中的 `etcd-raft` 模块是一个 raft 的标准实现，raftexample 通过这个 `etcd-raft` 模块构建了一个分布式的一致性的存储驱动，通过 raftexample 这个例子可以看下使用 raft 的设计思想。从这例子中可以看出 raft 是跟业务无关的，它只关心数据在几个 raft 实例之间的一致性，具体是什么数据它不关心。同时它也不关心数据持久化的问题，他只是提供一个 committed 状态，表示这个数据已经在多个实例之间达成共识，需要上层应用来实现持久化，也就是 apply。

`etcd-raft` 的实现主要在 etcd 源码 raft 包中，主要是 raft/node.go，raft/raft.go 这两个文件，前者暴露接口，后者是实际实现。本文暂不介绍 raft 实现，先从宏观上看下怎么使用 raft，以及 raft 是如何跟上层应用交互的。

`raftexample` 的 github 文档为：[raftexample](https://github.com/etcd-io/etcd/tree/main/contrib/raftexample)，根据文档，`raftexample` 主要包括三部分：
* a raft-backed key-value store：这个 key-value 存储就是 `kvstore`，具体是在 `contrib/raftexample/kvstore.go` 文件中。在 raftexample 这个示例中，扮演持久化存储的角色，实际实现是用 map 保存在内存中的。
* a REST API server：这个是一个 Http server，对外暴露了数据的 get、put 方法，用于数据的查询，添加。同时也暴露了 raft 集群配置更新的接口，比如添加实例、删除实例等。其实现主要是 `contrib/raftexample/httpapi.go` 中的 `httpKVAPI`。
* a raft consensus server：这个主要是说 raftNode，其实现为 `contrib/raftexample/raft.go`，raftNode 是对 etcd raft 模块的封装，后者代表一个 raft 实例，raftNode 还有其他功能，比如：WAL 日志管理、快照管理、网络层相关功能。

对外的 GET/PUT 请求首先发送到 REST server，然后 REST server 调用 kvStore 的 `Lookup` 以及 `Propose` 方法，完成数据的查询以及写入，我们知道，kvStore 不是直接操作其 map 进行读写，毕竟要经过 raft 模块的共识处理。上层应用通知 raft 模块来进行共识处理，主要是通过 channel 来完成的，在 raftexample 中，主要的 channel 有：proposeC、confChangeC、commitC。在 raftexample 中，其实现流程是：httpServer 收到 put 请求时，调用 kvStore 的 Propose 方法，后者将数据写入 proposeC 这个 channel，raftNode 模块消费这个 channel，并最终调用 etcd raft 模块的 Propose 进行处理。其中 raftNode 调用 raft 模块主要是通过 Node 这个接口来进行的，Node 接口定义的文件为 `raft/node.go`，定义大概如下：
```go
// Node represents a node in a raft cluster.
type Node interface {
	Tick()
	Campaign(ctx context.Context) error
	Propose(ctx context.Context, data []byte) error
	Ready() <-chan Ready
	Advance()
	ReadIndex(ctx context.Context, rctx []byte) error
	// 省去了其他方法和注释
}
```

参考《etcd 技术内幕》， raftexample 的整体架构如下。
![java-javascript](/img/in-post/all-in-one/2022-02-06-17-30-44.png)

下面大概从三个方面介绍 raftexample，也就是上面提到的三个模块 httpserver、kvstore、raftNode。另外，也通过 raftexample 来看下 snapshot 以及 wal 的实现逻辑，这两者作为单独的模块分析。

### httpserver
httpserver 的实现较为简单，其定义如下，包含了持久存储 kvstore，以及一个处理配置变化的 channel。
```go
type httpKVAPI struct {
	store       *kvstore
	confChangeC chan<- raftpb.ConfChange
}
```
其定义了几个请求处理方法，严格来说是根据请求的 verb 来处理请求：
* PUT: 存储数据。
* GET：查询数据。
* POST: 集群配置变化。
* DELETE: 集群删除节点。

列举一下主要的逻辑，省略其他逻辑：
```go
func (h *httpKVAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	key := r.RequestURI
	defer r.Body.Close()
	switch {
	case r.Method == "PUT":
		v, err := ioutil.ReadAll(r.Body)
		if err != nil {
			log.Printf("Failed to read on PUT (%v)\n", err)
			http.Error(w, "Failed on PUT", http.StatusBadRequest)
			return
		}
        
		// 调用 store 的 Propose 方法，后者写 proposeC
		h.store.Propose(key, string(v))

		// Optimistic-- no waiting for ack from raft. Value is not yet
		// committed so a subsequent GET on the key may return old value
		w.WriteHeader(http.StatusNoContent)
    case r.Method == "GET":
		// 调用 lookup 查询
		if v, ok := h.store.Lookup(key); ok {
			w.Write([]byte(v))
		} else {
			http.Error(w, "Failed to GET", http.StatusNotFound)
		}
	case r.Method == "POST":
		url, err := ioutil.ReadAll(r.Body)
		if err != nil {
			log.Printf("Failed to read on POST (%v)\n", err)
			http.Error(w, "Failed on POST", http.StatusBadRequest)
			return
		}

		nodeId, err := strconv.ParseUint(key[1:], 0, 64)
		if err != nil {
			log.Printf("Failed to convert ID for conf change (%v)\n", err)
			http.Error(w, "Failed on POST", http.StatusBadRequest)
			return
		}

		cc := raftpb.ConfChange{
			Type:    raftpb.ConfChangeAddNode,
			NodeID:  nodeId,
			Context: url,
        }
 		// 直接写 confChangeC channel
		h.confChangeC <- cc
	}
}
```
### raftNode
raftNode 是对 etcd raft 模块的封装，从上对接应用层（或者本身是个应用层，这个要看你怎么理解了），从下对接 raft 模块。从上对接主要是通过一些列 channel 来完成的，与 raft 通信，主要是通过 raft.Node 接口。raftNode 实现比较复杂，其定义如下：
```go
// A key-value stream backed by raft
type raftNode struct {
	proposeC    <-chan string            // proposed messages (k,v)
	confChangeC <-chan raftpb.ConfChange // proposed cluster config changes
	commitC     chan<- *string           // entries committed to log (k,v)
	errorC      chan<- error             // errors from raft session

	id          int      // client ID for raft session
	peers       []string // raft peer URLs
	join        bool     // node is joining an existing cluster
	waldir      string   // path to WAL directory
	snapdir     string   // path to snapshot directory
	getSnapshot func() ([]byte, error)
	lastIndex   uint64 // index of log at start

	confState     raftpb.ConfState
	snapshotIndex uint64
	appliedIndex  uint64

	// raft backing for the commit/error channel
	node        raft.Node
	raftStorage *raft.MemoryStorage
	wal         *wal.WAL

	snapshotter      *snap.Snapshotter
	snapshotterReady chan *snap.Snapshotter // signals when snapshotter is ready

	snapCount uint64
	transport *rafthttp.Transport
	stopc     chan struct{} // signals proposal channel closed
	httpstopc chan struct{} // signals http server to shutdown
	httpdonec chan struct{} // signals http server shutdown complete
}
```
上面有几个比较重要的字段是 channel，也都介绍过了，还有就是 `raft.Node` 也就是跟 etcd raft 通信的接口，还有就是 `*rafthttp.Transport` 负责网络层。其中 serveChannels 主要用于各个 channel 的生产和消费。其中一个 goroutine 读取 proposeC 和 confChangeC，另一个 channel 通过调用 raft 模块的 Ready() 方法获取 ready 实例，并放到 commitC 同道中，用于 kvstore 消费。

```go
func (rc *raftNode) serveChannels() {
	snap, err := rc.raftStorage.Snapshot()
	if err != nil {
		panic(err)
	}
	rc.confState = snap.Metadata.ConfState
	rc.snapshotIndex = snap.Metadata.Index
	rc.appliedIndex = snap.Metadata.Index

	defer rc.wal.Close()

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	// send proposals over raft
	go func() {
		confChangeCount := uint64(0)

		for rc.proposeC != nil && rc.confChangeC != nil {
			select {
			case prop, ok := <-rc.proposeC:
				if !ok {
					rc.proposeC = nil
				} else {
					// blocks until accepted by raft state machine
					rc.node.Propose(context.TODO(), []byte(prop))
				}

			case cc, ok := <-rc.confChangeC:
				if !ok {
					rc.confChangeC = nil
				} else {
					confChangeCount++
					cc.ID = confChangeCount
					rc.node.ProposeConfChange(context.TODO(), cc)
				}
			}
		}
		// client closed channel; shutdown raft if not already
		close(rc.stopc)
	}()

	// event loop on raft state machine updates
	for {
		select {
		case <-ticker.C:
			rc.node.Tick()

		// store raft entries to wal, then publish over commit channel
		case rd := <-rc.node.Ready():
			rc.wal.Save(rd.HardState, rd.Entries)
			if !raft.IsEmptySnap(rd.Snapshot) {
				rc.saveSnap(rd.Snapshot)
				rc.raftStorage.ApplySnapshot(rd.Snapshot)
				rc.publishSnapshot(rd.Snapshot)
			}
			rc.raftStorage.Append(rd.Entries)
			rc.transport.Send(rd.Messages)
			if ok := rc.publishEntries(rc.entriesToApply(rd.CommittedEntries)); !ok {
				rc.stop()
				return
			}
			rc.maybeTriggerSnapshot()
			rc.node.Advance()

		case err := <-rc.transport.ErrorC:
			rc.writeError(err)
			return

		case <-rc.stopc:
			rc.stop()
			return
		}
	}
}
```


### kvstore
kvstore 扮演持久化存储的角色，其定义如下，proposeC 就是跟 raftNOde 通信的 channel。
```go
// a key-value store backed by raft
type kvstore struct {
	proposeC    chan<- string // channel for proposing updates
	mu          sync.RWMutex
	kvStore     map[string]string // current committed key-value pairs
	snapshotter *snap.Snapshotter
}

// 查询方法就是直接读 kvStore
func (s *kvstore) Lookup(key string) (string, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	v, ok := s.kvStore[key]
	return v, ok
}

// 添加方法是写 proposeC 通道。
func (s *kvstore) Propose(k string, v string) {
	var buf bytes.Buffer
	if err := gob.NewEncoder(&buf).Encode(kv{k, v}); err != nil {
		log.Fatal(err)
	}
	s.proposeC <- buf.String()
}
```
其中 kvStore 的 readCommits 方法就是通过读取 commitC 通道，并写入 map.
```go
func (s *kvstore) readCommits(commitC <-chan *string, errorC <-chan error) {
	for data := range commitC {
		if data == nil {
			// done replaying log; new data incoming
			// OR signaled to load snapshot
			snapshot, err := s.snapshotter.Load()
			if err == snap.ErrNoSnapshot {
				return
			}
			if err != nil {
				log.Panic(err)
			}
			log.Printf("loading snapshot at term %d and index %d", snapshot.Metadata.Term, snapshot.Metadata.Index)
			if err := s.recoverFromSnapshot(snapshot.Data); err != nil {
				log.Panic(err)
			}
			continue
		}

		var dataKv kv
		dec := gob.NewDecoder(bytes.NewBufferString(*data))
		if err := dec.Decode(&dataKv); err != nil {
			log.Fatalf("raftexample: could not decode message (%v)", err)
		}
        s.mu.Lock()
		// 写 map
		s.kvStore[dataKv.Key] = dataKv.Val
		s.mu.Unlock()
	}
	if err, ok := <-errorC; ok {
		log.Fatal(err)
	}
}
```

### 快照

### wal 日志
raftexample 同样没有实现自己的 wal，而是直接使用了 etcd 中的 wal 实现，wal 作为 raftNode 的一个字段。这里只关注 wal 是怎么使用的，写 wal 的时机是什么。在 raftNode 的定义中，关于 wal 有两个字段：
```go
// A key-value stream backed by raft
type raftNode struct {
	waldir      string   // path to WAL directory
    wal         *wal.WAL
    // 省略其他字段
}
```
第一个是目录，第二个是 wal 的定义，从 WAL 的定义看，包含了很多东西，暂时先不关注 WAL 的定义。
从下面代码看，从 raft 模块的 Ready() 接口读到数据之后，立刻写 wal，也就是经过共识的数据写 wal，具体如下：
```go
func (rc *raftNode) serveChannels() {
	// 省略代码
	// event loop on raft state machine updates
	for {
		select {
		case <-ticker.C:
			rc.node.Tick()

		// store raft entries to wal, then publish over commit channel
        case rd := <-rc.node.Ready():
 			// 写 wal
			rc.wal.Save(rd.HardState, rd.Entries)
			if !raft.IsEmptySnap(rd.Snapshot) {
				rc.saveSnap(rd.Snapshot)
				rc.raftStorage.ApplySnapshot(rd.Snapshot)
				rc.publishSnapshot(rd.Snapshot)
			}
			rc.raftStorage.Append(rd.Entries)
			rc.transport.Send(rd.Messages)
			if ok := rc.publishEntries(rc.entriesToApply(rd.CommittedEntries)); !ok {
				rc.stop()
				return
			}
			rc.maybeTriggerSnapshot()
			rc.node.Advance()
 			// 省略代码
		}
	}
}
``` 

后面再详细分析下 snapshot 以及 wal 的实现，明天要开工了，暂时先不分析了。

