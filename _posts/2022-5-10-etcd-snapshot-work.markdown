---
layout:     post
title:      "Etcdctl snapshot 命令是怎么工作的"
subtitle:   " \"snapshot save 实现细节分析\""
date:       2022-5-10 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - Etcd
---

Etcd 运维过程中，可以通过下面命令对 Etcd 中的数据进行保存。那运行这个命令的时候后端都发生了啥呢？本文尝试研究一下，本文基于的 Etcd 版本是 3.4.3。
```s
# 将数据保存为名为 snapshot.db 的快照
etcdctl --endpoints $ENDPOINT snapshot save snapshot.db
```

### clientv3 snapshot 客户端实现
clientv3 的 snapshot 客户端实现位于代码 `etcd/clientv3/snapshot/v3_snapshot.go`，主干代码如下，下面这些代码是 gRPC 客户端的上一层，代码比较简单，但是涉及到一些 Golang io 读写的常用操作，所以还是贴一下，下面通过 `io.Copy` 将数据流从 reader 拷贝到文件，这个方法将 `io.EOF` 看做数据结束的标志，而不是错误。
```go
// 从 remote 获取 etcd 快照，并保存到本地一个文件中。
func (s *v3Manager) Save(ctx context.Context, cfg clientv3.Config, dbPath string) error {
	cli, _ := clientv3.New(cfg)
	defer cli.Close()
	// 先写到临时文件中，写完之后通过 rename 重命名，看起来像原子操作
	partpath := dbPath + ".part"
	defer os.RemoveAll(partpath)
	// 打开本地那个文件，不存在就创建
	var f *os.File
	f, _ = os.OpenFile(partpath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, fileutil.PrivateFileMode)
	now := time.Now()
	var rd io.ReadCloser
	rd, _ = cli.Snapshot(ctx) // Snapshot 接口返回的是一个 io.ReadCloser，可读，可关闭
	_, _ = io.Copy(f, rd)  // 调用 io.Copy 从返回的 reader 中读数据，并写到本地文件中 
	err = fileutil.Fsync(f) // 调用 *File.Sync，将数据从 pagecache 刷新到磁盘
	f.Close() // 关闭本地文件
	os.Rename(partpath, dbPath); // 重命名文件
	return nil
}
```
上面代码中，调用了 `cli.Snapshot(ctx)` 这个方法，这个方法是定义在 `Maintenance` 接口中的，其实现者是 `maintenance`，我们只关注下`maintenance` 的 Snapshot 这个方法。
```go
type Client struct { // clientv3 客户端实现
	KV  // key/value CRUD 操作
	Maintenance // snap 操作是 Maintenance 定义的。
	// ...
}
```
`maintenance` 中实现的方法如下，封装了 gRPC 中的 remote 接口，在 gRPC Snapshot 接口的定义中，服务端是流式返回数据，`SnapshotResponse` 前带有 `stream` 关键字，所在客户端需要再一个 for 循环中，不断调用 `ss.Recv` 方法来接收数据，这个方法返回的数据就是在 proto 中定义的 `SnapshotResponse`。
```protobuf
service Maintenance {
  // Snapshot sends a snapshot of the entire backend from a member over a stream to a client.
  rpc Snapshot(SnapshotRequest) returns (stream SnapshotResponse) {
      option (google.api.http) = {
        post: "/v3/maintenance/snapshot"
        body: "*"
    };
  }
}
```
`maintenance.Snapshot` 具体实现如下，其对 gRPC 的接口进行了封装，返回的数据类型是一个 `io.ReadCloser`，一个可以读和关闭的接口，其调用者（就是上面的 v3Manager）通过 `io.Copy(file, readCloser)` 读这个接口，直接将内容拷贝到了 `etcdctl snapshot save` 命令指定的文件中。比较巧妙的是，下面实现中，通过 `io.Pipe` 进行了桥接。因为在 gRPC 的实现中，需要通过 `ss.Recv` 接收一个一个数据块，Pipe 将所有的数据库整合成一个数据流返回给上层。另外 ss.Recv 结束时，会返回 io.EOF，读到这个错误之后，会通过 `pipeWriter.CloseWithError` 传递给 pipeReader，所以 reader 收到的也是 io.EOF 错误，这个错误又被 io.Copy 当做一个正常结束的标志了。
```go
func (m *maintenance) Snapshot(ctx context.Context) (io.ReadCloser, error) {
	ss, err := m.remote.Snapshot(ctx, &pb.SnapshotRequest{}, append(m.callOpts, withMax(defaultStreamMaxRetries))...)
	if err != nil {
		return nil, toErr(ctx, err)
	}

	pr, pw := io.Pipe() // 创建一个管道，返回 reader 和 writer
	go func() {   // gRPC 数据读取和 pipe 的写入放到一个单独的 goroutine 里
		for {
			resp, err := ss.Recv()
			if err != nil {
				pw.CloseWithError(err) // 将 err 保存到 pipe 内部，返回给 pipe.Reader
				return
			}
			if resp == nil && err == nil { // 这个错误其实总是 nil 的
				break // 跳出 for 循环
			}
			if _, werr := pw.Write(resp.Blob); werr != nil {
				pw.CloseWithError(werr)
				return
			}
		}
		pw.Close() // writer.Close，会将错误标志置为 io.EOF
	}()
	return &snapshotReadCloser{ctx: ctx, ReadCloser: pr}, nil
}
```
我们在花点时间看下服务端流式 gRPC 返回的数据类型 `SnapshotResponse`，这个也就是上面 `ss.Recv()` 返回的数据类型，在上面实现中，只用到了 `Blob` 这个字段，也就是一个数据块，在服务端实现中，我们看下是怎么分割数据块的。
```go
type SnapshotResponse struct {
	Header *ResponseHeader `protobuf:"bytes,1,opt,name=header" json:"header,omitempty"`
	RemainingBytes uint64 `protobuf:"varint,2,opt,name=remaining_bytes,json=remainingBytes,proto3" json:"remaining_bytes,omitempty"`
	Blob []byte `protobuf:"bytes,3,opt,name=blob,proto3" json:"blob,omitempty"`
}
```

### snapshot 服务端实现
通过 rpc.pb.go 文件中 `RegisterMaintenanceServer` 方法查找，发现 maintenace Server 的实现者是`maintenanceServer`，定义在文件 `etcd/etcdserver/api/v3rpc/maintenance.go` 中，其实现如下，下面方法首先调用了 mvcc `backend.Snapshot` 方法，这个方法返回的是一个 `Snapshot` 接口，这个接口有个 `WriteTo(w io.Writer)` 方法，可以将整个 boltdb 的数据写到一个 writer 中去，这里也是调用的 boltDB 事务 *Tx 的 `WriteTo` 方法。

总结下就是后端 boltDB 返回了一个接口，通过这个方法可以将整个 boltdb 的数据写到一个 writer 中去，这个 writer 是需要调用者提供的。通过下面的方法实现，我们看到 `maintenanceServer` 这个调用者提供的 writer 是一个 pipeWriter，其在一个 goroutine 中不断写入 snapshot的数据。
```go
func (ms *maintenanceServer) Snapshot(sr *pb.SnapshotRequest, srv pb.Maintenance_SnapshotServer) error {
	snap := ms.bg.Backend().Snapshot()
	pr, pw := io.Pipe()
	defer pr.Close()

	go func() {
		snap.WriteTo(pw) // 一直写数据
		if err := snap.Close(); err != nil { // 写完之后，调用 Close 关闭底层事务 Tx
			if ms.lg != nil {
				ms.lg.Warn("failed to close snapshot", zap.Error(err))
			} else {
				plog.Errorf("error closing snapshot (%v)", err)
			}
		}
		pw.Close() // 同时关闭 pipeWriter，pipe.Reader 收到 io.EOF 错误
	}()

	h := sha256.New()
	br := int64(0) // br 统计已经发送的字节数
	buf := make([]byte, 32*1024) // 一个 32k 的 buffer
	sz := snap.Size()
	for br < sz {
		n, err := io.ReadFull(pr, buf)
		if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
			return togRPCError(err)
		}
		br += int64(n)
		resp := &pb.SnapshotResponse{
			RemainingBytes: uint64(sz - br),
			Blob:           buf[:n],
		}
		if err = srv.Send(resp); err != nil { // 每读满 32k，就发送一个 response
			return togRPCError(err)
		}
		h.Write(buf[:n]) // 同时写入到 sha256 hash 中
	}

	// send sha
	sha := h.Sum(nil)   // 计算 hash 值，通过最后一个 response 返回
	hresp := &pb.SnapshotResponse{RemainingBytes: 0, Blob: sha}
	if err := srv.Send(hresp); err != nil {
		return togRPCError(err)
	}
	return nil
}
```
上面代码中，pipe 的写入和读出分别在两个 goroutine 中，写入的时候没有要求，直接写就可以了，写完之后就 Close。关键是读出部分的逻辑，在上面代码中，读数据的时候，使用了一个 32k 的 buffer，每 32k 返回一个 response，也就是返回一块字节数据，直到所有数据都发送完，最后一块数据可能不够 32k，不过不要紧，通过 `n, err := io.ReadFull(pr, buf)` 中的 n 可以知道实际读了多少数据，io.ReadFull 在没读满 32k 的时候，会返回错误 `io.ErrUnexpectedEOF`，这个可当做正常的 case 处理。

另外，代码最后将 hash 当做一个最后一个 response，那在实际数据中，怎么区分这个 hash 和实际的数据库文件呢？答案是：这个 sha256 hash 是固定大小的，256 字节，恢复的时候，隔离开这 256 字节就好了。

另外boltDB 事务 *Tx 的 `WriteTo` 方法需要实现开启一个读事务，所以理论上执行 `etcdctl snapshot save` 做快照的时候，会阻塞写事务（如果数据库文件较大，会导致写请求超时），所以不要频繁的做快照，并且要在业务低谷的时候做快照。

### 参考
`proto` 文件中，Snapshot 接口的定义如下，其中 `google.api.http` 用于生成一个 httpserver，定义这个 http 接口与 grpc 接口的映射，具体参考下面的 grpc-gateway，同时下面有一个 `stream` 关键字，表示 gRPC server 是流式返回数据。
```proto
service Maintenance {
  // Snapshot sends a snapshot of the entire backend from a member over a stream to a client.
  rpc Snapshot(SnapshotRequest) returns (stream SnapshotResponse) {
      option (google.api.http) = {
        post: "/v3/maintenance/snapshot"
        body: "*"
    };
  }
}
```
[gRPC-Gateway使用指南](https://www.liwenzhou.com/posts/Go/grpc-gateway/)

[grpc-gateway](https://github.com/grpc-ecosystem/grpc-gateway)