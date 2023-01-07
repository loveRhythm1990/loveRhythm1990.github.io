---
layout:     post
title:      "基于resourceversion的List与Watch"
date:       2021-03-26 15:22:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
---

K8s文档[Kubernetes API Concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/)介绍了很多关于ListWatch的一些知识，值得好好研究，这里结合文档进行理解一下。

ResourceVersion其实是etcd内部的`ModifiedIndex`，是全局唯一且递增的正整数，每次在etcd集群中对key有update操作的时候就会递增。在K8s集群中，可以看做与具体资源无关的一个逻辑时钟。对于一般资源（比如pod）来说，它的ResourceVersion就是其发生update时的最新版本号，对于List资源来说，其版本号是给客户端构建待返回的List那个时刻的版本号。

##### ListOptions 数据结构
先贴一些`ListOptions`的数据结构，后面会介绍部分字段。
```go
type ListOptions struct {
	TypeMeta `json:",inline"`
	LabelSelector string `json:"labelSelector,omitempty" protobuf:"bytes,1,opt,name=labelSelector"`
	FieldSelector string `json:"fieldSelector,omitempty" protobuf:"bytes,2,opt,name=fieldSelector"`
  // 是否是watch请求
	Watch bool `json:"watch,omitempty" protobuf:"varint,3,opt,name=watch"`
  // 是否允许server发送BOOKMARK事件，只在watch请求中有效
	AllowWatchBookmarks bool `json:"allowWatchBookmarks,omitempty" protobuf:"varint,9,opt,name=allowWatchBookmarks"`
  ResourceVersion string `json:"resourceVersion,omitempty" protobuf:"bytes,4,opt,name=resourceVersion"`
  // v1.19新加的字段，指定resourceVersion的行为
	ResourceVersionMatch ResourceVersionMatch `json:"resourceVersionMatch,omitempty" protobuf:"bytes,10,opt,name=resourceVersionMatch,casttype=ResourceVersionMatch"`
	TimeoutSeconds *int64 `json:"timeoutSeconds,omitempty" protobuf:"varint,5,opt,name=timeoutSeconds"`
  // 分页时一次response中最多的item数
	Limit int64 `json:"limit,omitempty" protobuf:"varint,7,opt,name=limit"`
  // 从server中获取剩下的item，这个字段要跟server返回的continue一致，后者保存在返回结果的ListMeta结构体中
	Continue string `json:"continue,omitempty" protobuf:"bytes,8,opt,name=continue"`
}
```

##### Watch与Watch BookMark
K8s以watch机制来同步资源的增量修改。一般是通过Reflector来实现的，使用watch监听资源变化时，首先通过list接口获取资源全量，调用list时，会返回获得一个ResourceVersion，表示当前的资源版本号，后续watch会根据这个ResourceVersion进行监听，只监听大于这个版本号的事件。

K8s中的etcd3默认值储存5分钟内的事件，当客户端请求一个已经被删除的resourceVersion对应的资源时，就会收到错误码为`410 Gone`的错误。当客户端收到这个错误的时候，应该清除本地cache，重新进行list。

BookMark机制的引入就是定期更新reflector中记录的最新的ResourceVersion，哪怕最新的ResourceVersion没有这个Reflector感兴趣的事件，更新ResourceVersion靠的是`BookMark`事件，这个事件只有资源种类以及版本号，没有具体资源信息，如下：
```json
{
  "type": "ADDED",
  "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "10596", ...}, ...}
}
{
  "type": "BOOKMARK",
  "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "12746"} }
}
```
当watch断开重连的时候，只需要根据最新的ResourceVersion进行重新watch，而因为`BookMark`事件的存在，server大概率还是有这个最新的版本号的，即etcd中还存留其数据，这样就不需要进行重新全量list了。如果发起watch的时候，客户端发送的resourceVersion太小，会报下面错误，这个在local volume provisioner中经常遇到。
```s
W0224 15:34:39.762384       1 reflector.go:302] k8s.io/client-go/informers/factory.go:133: watch of *v1.PersistentVolume ended with: too old resource version: 8834140265 (8835125434)
```
关于这个问题，[当 K8s 集群达到万级规模，阿里巴巴如何解决系统各组件性能问题？](https://zhuanlan.zhihu.com/p/83681938)这篇文章里有更详细的解释，这篇文章写的很好。

##### 通过chunks的方式返回较大的查询数据
如果server一次返回的数据量较大，会给服务端带来很大的压力，在list的时候可以在List中指定一次获取的资源数（比如一次只拿500个pod），字段是`Limit`；同时server端返回数据中，metadata字段设置了一个`continue`，我们可以拿着这个continue token继续获取剩下的资源（`continue`也是ListOption的一个字段），如果`continue`字段为空，说明没有剩下字段了。这个交互过程如下所示，注意limit以及continue字段的设置`"continue": "ENCODED_CONTINUE_TOKEN"`。

**一次最多获取500个**，返回了一个token:`"continue": "ENCODED_CONTINUE_TOKEN",`
```s
GET /api/v1/pods?limit=500
---
200 OK
Content-Type: application/json

{
  "kind": "PodList",
  "apiVersion": "v1",
  "metadata": {
    "resourceVersion":"10245",
    "continue": "ENCODED_CONTINUE_TOKEN",
    ...
  },
  "items": [...] // returns pods 1-500
}
```
**根据token获取剩下的数据**，直到continue字段为空。
```s
GET /api/v1/pods?limit=500&continue=ENCODED_CONTINUE_TOKEN
---
200 OK
Content-Type: application/json

{
  "kind": "PodList",
  "apiVersion": "v1",
  "metadata": {
    "resourceVersion":"10245",
    "continue": "ENCODED_CONTINUE_TOKEN_2",
    ...
  },
  "items": [...] // returns pods 501-1000
}
```


##### ListOption 配置resourceversion
K8s client的`Get`方法，`List`方法以及`Watch`方法都支持在请求时指定`resourceVersion`参数。该参数是个字符串，如果不设置，默认值是空字符串`""`。
```go
type GetOptions struct {
	TypeMeta `json:",inline"`
	// resourceVersion sets a constraint on what resource versions a request may be served from.
	// See https://kubernetes.io/docs/reference/using-api/api-concepts/#resource-versions for
	// details.
	//
	// Defaults to unset
	// +optional
	ResourceVersion string `json:"resourceVersion,omitempty" protobuf:"bytes,1,opt,name=resourceVersion"`
}
```
对于`Get`方法在getOption中指定，其行为如下：

**Get:**

| 不设置 | 设置为0 | 设置为非零值 |
|  ----  | ----  | ---- |
| Most Recent | Any | Not older than |

其中`Most Recent`是指从etcd中获取最新数据，需要透传etcd。

对于`List`方法，在`ListOption`中指定。另外K8s`v1.19+`版本支持参数`resourceVersionMatch`，用来定义resourceVersion的行为，指定`resourceVersionMatch`更直观，语义更清晰一些。鉴于生产环境中用1.19的还比较少，先不考虑用此参数的情况。`List`的行为如下：

| 分页参数 | resourceVersion unset | resourceVersion="0" | resourceVersion="{value other than 0}" |
| ---- |  ----  | ----  | ---- |
| limit unset | Most Recent | Any | Not older than |
| limit=n, continue unset | Most Recent | Any | Exact |
| limit=n, continue=token	| Continue Token, Exact | Invalid, treated as Continue Token, Exact | Not older than |

从表中可以看出，一旦使用`continue`字段继续读取剩下的数据的时候，就不能设置`resourceVersion`这个字段了。

关于`Most Recent`等字段说明如下:
* Most Recent: 从ectd拿最新的数据，Return data at the most recent resource version. The returned data must be consistent (i.e. served from etcd via a quorum read)
* Any: 返回任一版本数据，数据有可能很旧了。
* Not older than: 至少返回ResourceVersion比这个大的数据。对于List类型返回数据能保证`ListMeta`中的版本号比这个大，这个容易理解，就是构建这个List时的版本号，但是List里面的Item（具体某个资源，比如某个Pod），其`ObjectMeta`中的ResourceVersion有可能比这个小，这个也容易理解，因为Pod发生了修改，才改ResourceVersion的值。
* Exact: 获取特定版本号，没有了就返回`Gone`
* Continue Token, Exact: Return data at the resource version of the initial paginated list call. The returned Continue Tokens are responsible for keeping track of the initially provided resource version for all paginated list calls after the initial paginated list call.

watch的选项差不多，不翻译了

| resourceVersion unset | resourceVersion="0" | resourceVersion="{value other than 0}" |
|  ----  | ----  | ---- |
| Get State and Start at Most Recent | Get State and Start at Any | Start at Exact |

#### 参考
[当 K8s 集群达到万级规模，阿里巴巴如何解决系统各组件性能问题？](https://zhuanlan.zhihu.com/p/83681938)