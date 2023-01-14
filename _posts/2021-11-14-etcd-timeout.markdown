---
layout:     post
title:      "关于 Etcd 超时：read-only request took too long to execute 的零零碎碎"
date:       2021-11-14 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Etcd
---

## 背景
etcd 的日志中，经常有下面的 warning 信息，这个东西本没有好分析的，只不过自己太懒了，只能靠一边写博客一边分析，这样才有点动力。
```s
2021-10-24 01:36:29.048037 W | etcdserver: read-only range request "key:\"/registry/roles/cattle-global-data/mcapprevision-9gc5b-mr-u\" " with result "range_response_count:0 size:8" took too long (189.874669ms) to execute
2021-10-24 01:36:29.048394 W | etcdserver: read-only range request "key:\"/registry/apps.kruise.io/containerrecreaterequests\" range_end:\"/registry/apps.kruise.io/containerrecreaterequestt\" count_only:true " with result "range_response_count:0 size:8" took too long (204.0239ms) to execute
```

首先社区有相关 issue:[etcdserver: read-only range request took too long with etcd 3.2.24 #70082](https://github.com/kubernetes/kubernetes/issues/70082)

从大家的讨论来看，基本上是磁盘性能问题，本文看看研究一下，能否获取进一步的信息。本文研究的是 etcd 3.4.3 版本。

## 分析
日志是通过函数 `warnOfExpensiveGenericRequest` 打印的。
```go
plog.Warningf("%srequest %q with result %q took too long (%v) to execute", prefix, reqStringer.String(), result, d)
```
从调用处看，调用处不多，逐个分析一下。
![java-javascript](/img/in-post/all-in-one/2021-11-14-10-32-14.png)

从下面函数 `warnOfExpensiveReadOnlyRangeRequest` 开始分析，其调用处只有一个地方，由此可见，这个日志包含的信息也不多，是指从收到请求到返回请求的总时延，中间还包括很多动作。
```go
func (s *EtcdServer) Range(ctx context.Context, r *pb.RangeRequest) (*pb.RangeResponse, error) {
	trace := traceutil.New("range",
		s.getLogger(),
		traceutil.Field{Key: "range_begin", Value: string(r.Key)},
		traceutil.Field{Key: "range_end", Value: string(r.RangeEnd)},
	)
	ctx = context.WithValue(ctx, traceutil.TraceKey, trace)

	var resp *pb.RangeResponse
	var err error
	defer func(start time.Time) {
		warnOfExpensiveReadOnlyRangeRequest(s.getLogger(), start, r, resp, err)
		if resp != nil {
			trace.AddField(
				traceutil.Field{Key: "response_count", Value: len(resp.Kvs)},
				traceutil.Field{Key: "response_revision", Value: resp.Header.Revision},
			)
		}
		trace.LogIfLong(traceThreshold)
	}(time.Now())

	if !r.Serializable {
		err = s.linearizableReadNotify(ctx)
		trace.Step("agreement among raft nodes before linearized reading")
		if err != nil {
			return nil, err
		}
	}
	chk := func(ai *auth.AuthInfo) error {
		return s.authStore.IsRangePermitted(ai, r.Key, r.RangeEnd)
	}

	get := func() { resp, err = s.applyV3Base.Range(ctx, nil, r) }
	if serr := s.doSerialize(ctx, chk, get); serr != nil {
		err = serr
		return nil, err
	}
	return resp, err
}
```
`EtcdServer` 结构体是接口 `Server` 的实现，在文件 `etcd/etcdserver/server.go` 中，是 etcd 服务端的核心接口，定义了 etcd 服务端的主要功能，这个结构体看上去巨大，应该是 etcd 的主要入口和实现，那么这里的 Range 方法应该就是只读的查询请求的入口。

> NewServer() 函数中会完成 EtcdServer 的初始化，也是 etcd 服务端声明周期的起始。openBackend() 启动一个后台 goroutine 完成 Backend 实例的初始化。

## 附录
### defer 函数参数求值
defer 函数的参数，是在关键字声明的时候求值的，参考 [Evaluation of defer arguments in Go (Golang)](https://golangbyexample.com/defer-arguments-evaluation-go/)
```go
package main
import "fmt"
func main() {
	sample := "abc"
	defer fmt.Printf("In defer sample is: %s\n", sample)
	sample = "xyz"
}
// 输出为 abc
```

### RangeRequest / RangeResponse
错误信息中其实把所有的请求参数都打印出来了，没有打印出来的为默认值，比如：
```s
2021-10-24 01:36:29.048394 W | etcdserver: read-only range request "key:\"/registry/apps.kruise.io/containerrecreaterequests\" range_end:\"/registry/apps.kruise.io/containerrecreaterequestt\" count_only:true " with result "range_response_count:0 size:8" took too long (204.0239ms) to execute
```
说明指定了 RangeRequest 中的三个字段 `key`、`range_end`、`count_only`，RangeRequest 的定义参考：`etcd/etcdserver/etcdserverpb/rpc.pb.go`

对于 RangeResponse，只打印了 count（返回的 Kvs slice 长度），以及 size，
```go
type RangeResponse struct {
	Header *ResponseHeader `protobuf:"bytes,1,opt,name=header" json:"header,omitempty"`
	// kvs is the list of key-value pairs matched by the range request.
	// kvs is empty when count is requested.
	Kvs []*mvccpb.KeyValue `protobuf:"bytes,2,rep,name=kvs" json:"kvs,omitempty"`
	// more indicates if there are more keys to return in the requested range.
	More bool `protobuf:"varint,3,opt,name=more,proto3" json:"more,omitempty"`
	// count is set to the number of keys within the range when requested.
	Count int64 `protobuf:"varint,4,opt,name=count,proto3" json:"count,omitempty"`
}
```
RangeRequest 结构体定义如下：
```go
type RangeRequest struct {
	// key is the first key for the range. If range_end is not given, the request only looks up key.
	Key []byte `protobuf:"bytes,1,opt,name=key,proto3" json:"key,omitempty"`
	// range_end is the upper bound on the requested range [key, range_end).
	// If range_end is '\0', the range is all keys >= key.
	// If range_end is key plus one (e.g., "aa"+1 == "ab", "a\xff"+1 == "b"),
	// then the range request gets all keys prefixed with key.
	// If both key and range_end are '\0', then the range request returns all keys.
	RangeEnd []byte `protobuf:"bytes,2,opt,name=range_end,json=rangeEnd,proto3" json:"range_end,omitempty"`
	// limit is a limit on the number of keys returned for the request. When limit is set to 0,
	// it is treated as no limit.
	Limit int64 `protobuf:"varint,3,opt,name=limit,proto3" json:"limit,omitempty"`
	// revision is the point-in-time of the key-value store to use for the range.
	// If revision is less or equal to zero, the range is over the newest key-value store.
	// If the revision has been compacted, ErrCompacted is returned as a response.
	Revision int64 `protobuf:"varint,4,opt,name=revision,proto3" json:"revision,omitempty"`
	// sort_order is the order for returned sorted results.
	SortOrder RangeRequest_SortOrder `protobuf:"varint,5,opt,name=sort_order,json=sortOrder,proto3,enum=etcdserverpb.RangeRequest_SortOrder" json:"sort_order,omitempty"`
	// sort_target is the key-value field to use for sorting.
	SortTarget RangeRequest_SortTarget `protobuf:"varint,6,opt,name=sort_target,json=sortTarget,proto3,enum=etcdserverpb.RangeRequest_SortTarget" json:"sort_target,omitempty"`
	// serializable sets the range request to use serializable member-local reads.
	// Range requests are linearizable by default; linearizable requests have higher
	// latency and lower throughput than serializable requests but reflect the current
	// consensus of the cluster. For better performance, in exchange for possible stale reads,
	// a serializable range request is served locally without needing to reach consensus
	// with other nodes in the cluster.
	Serializable bool `protobuf:"varint,7,opt,name=serializable,proto3" json:"serializable,omitempty"`
	// keys_only when set returns only the keys and not the values.
	KeysOnly bool `protobuf:"varint,8,opt,name=keys_only,json=keysOnly,proto3" json:"keys_only,omitempty"`
	// count_only when set returns only the count of the keys in the range.
	CountOnly bool `protobuf:"varint,9,opt,name=count_only,json=countOnly,proto3" json:"count_only,omitempty"`
	// min_mod_revision is the lower bound for returned key mod revisions; all keys with
	// lesser mod revisions will be filtered away.
	MinModRevision int64 `protobuf:"varint,10,opt,name=min_mod_revision,json=minModRevision,proto3" json:"min_mod_revision,omitempty"`
	// max_mod_revision is the upper bound for returned key mod revisions; all keys with
	// greater mod revisions will be filtered away.
	MaxModRevision int64 `protobuf:"varint,11,opt,name=max_mod_revision,json=maxModRevision,proto3" json:"max_mod_revision,omitempty"`
	// min_create_revision is the lower bound for returned key create revisions; all keys with
	// lesser create revisions will be filtered away.
	MinCreateRevision int64 `protobuf:"varint,12,opt,name=min_create_revision,json=minCreateRevision,proto3" json:"min_create_revision,omitempty"`
	// max_create_revision is the upper bound for returned key create revisions; all keys with
	// greater create revisions will be filtered away.
	MaxCreateRevision int64 `protobuf:"varint,13,opt,name=max_create_revision,json=maxCreateRevision,proto3" json:"max_create_revision,omitempty"`
}
```

## 参考

[etcd 中线性一致性读的具体实现](https://zhengyinyong.com/post/etcd-linearizable-read-implementation/)