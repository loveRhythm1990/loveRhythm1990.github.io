---
layout:     post
title:      "Kubernetes API Concepts文档阅读"
date:       2020-08-22 15:22:00
author:     "weak old dog"
header-img-credit: false
tags:
    - k8s
---

文档阅读笔记，原文[Kubernetes API Concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/)

#### Standard API terminology 
Kubernetes generally leverages standard RESTful terminology to describe the API concepts：
* **resource type**是在URL中使用的名字，如：pods, namespaces, services
* 所有的resource type都有一个具体的json表示模式（their object schema），称为kind
* A list of instances of a resource type is known as a collection
* A single instance of the resource type is called a resource

所有资源分为cluster scope以及namespaced，访问模式分别为`/apis/GROUP/VERSION/*`，`/apis/GROUP/VERSION/namespaces/NAMESPACE/*`，namespace scoped资源在namespace被删除时，会被级联删除。

有些资源类型有subresource，用这种资源下的subpath来表示
* Cluster-scoped subresouce: `GET /apis/GROUP/VERSION/RESOURCETYPE/NAME/SUBRESOURCE`
* Namespace-scoped subresource: `GET /apis/GROUP/VERSION/namespaces/NAMESPACE/RESOURCETYPE/NAME/SUBRESOURCE`

#### Efficient detection of changes 
为了让client了解当前集群的的状态，所有的K8s都支持watch增量更新，所有的K8s资源都有个`resourceVersion`字段，表示存储在底层数据库的资源的版本，当获取一种资源的集合（collection）的时候，从service端返回的response都包含一个`resourceVersion`，这个`resourceVersion`就可以初始化一个watch。server端会返回在resourceVersion之后发生的所有变化，包括create/delete/update。因此client可以获取当前状态并不会丢失事件，如果watch连接断了，可以起一个新的连接，并从上次断开的resourceVersion开始重新watch。举例说明：
1. 列出test namespace下的所有pod
```json
 GET /api/v1/namespaces/test/pods
 ---
 200 OK
 Content-Type: application/json
 {
   "kind": "PodList",
   "apiVersion": "v1",
   "metadata": {"resourceVersion":"10245"},
   "items": [...]
 }
```
2. 从resourceVersion开始，监听所有的事件，每个事件作为单独的json ojbect返回。
```json
GET /api/v1/namespaces/test/pods?watch=1&resourceVersion=10245
 ---
 200 OK
 Transfer-Encoding: chunked
 Content-Type: application/json
 {
   "type": "ADDED",
   "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "10596", ...}, ...}
 }
 {
   "type": "MODIFIED",
   "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "11020", ...}, ...}
 }
 ...
```
一般K8s server只会保存一段时间内的改动，使用etcd3的集群默认保存过去5分钟的改动，当watch请求因为资源的历史版本不可用而失败的时候，客户端必须识别这种情况下的错误码**410 Gone**，并清除本地缓存，执行list操作，并从list返回的resourceVersion开始watch。大多数客户端有标准的工具做这些事情，在go语言里，这称作`Reflector`，代码在`k8s.io/client-go/cache`。

##### Watch bookmarks
为减轻历史改动时间窗口较小（etcd3的默认5分钟）的问题，引入了`bookmark`watch事件的概念。bookmark事件是一种特殊的事件，这个事件返回的Object只包含`resourceVersion`字段，表明这个字段之前的Object都已经同步给client了。
```json
GET /api/v1/namespaces/test/pods?watch=1&resourceVersion=10245&allowWatchBookmarks=true
---
200 OK
Transfer-Encoding: chunked
Content-Type: application/json
{
    "type": "ADDED",
    "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "10596", ...}, ...}
}
...
{
    "type": "BOOKMARK",
    "object": {"kind": "Pod", "apiVersion": "v1", "metadata": {"resourceVersion": "12746"} }
}
```
在watch request的请求中设置`allowWatchBookmarks=true`来启用bookmark，但是client不能对server对bookmark的行为做任何假设。

#### Retrieving large results sets in chunks 
在大集群中，list资源集合的时候，数据量可能非常大，可能会影响server以及client的性能，比如，一个集群中可能有数万个pod，每个pod的json数据有1-2k，列出集群内的所有pod返回的response大概有10-20M，这会消耗很多服务端资源，从K8s 1.9开始，服务端开始支持将大的response拆成小数据（chunk）返回，并且所有chunk保持一致性。

为了实现chunk返回，需要添加两个参数`limit`以及`continue`。请求的时候使用limit指定最大的item数量，返回的数据里如果`continue`不为空则说明还有数据需要接受，否则表示数据接收完了，比如，假设一共有1253个pod：
1. 指定每次最多拿500个。
```json
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
2. 继续之前的调用，拿下一个500个数据。
```json
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
3. 获取剩下的253个pod
```json
 GET /api/v1/pods?limit=500&continue=ENCODED_CONTINUE_TOKEN_2
 ---
 200 OK
 Content-Type: application/json
 {
   "kind": "PodList",
   "apiVersion": "v1",
   "metadata": {
     "resourceVersion":"10245",
     "continue": "", // continue token is empty because we have reached the end of the list
     ...
   },
   "items": [...] // returns pods 1001-1253
 }
```
要注意，在所有返回的数据中，所有的`resourceVersion`都是一致的，均为10245.

#### Alternate representations of resources 
默认K8s以json格式返回数据，content type设置为`application/json`，client为了性能，可以选择使用protobuf，K8s Api支持标准的HTTP协议，只要在Http header里指定`Accept`就好了，例子如下：
1. 列出集群中所有的Pod，要求以Protobuf的形式返回
```json
 GET /api/v1/pods
 Accept: application/vnd.kubernetes.protobuf
 ---
 200 OK
 Content-Type: application/vnd.kubernetes.protobuf
 ... binary encoded PodList object
```
2. 发送到时候以protobuf发送（设置Content-Type），接收的时候以json接收
```json
 POST /api/v1/namespaces/test/pods
 Content-Type: application/vnd.kubernetes.protobuf
 Accept: application/json
 ... binary encoded Pod object
 ---
 200 OK
 Content-Type: application/json
 {
   "kind": "Pod",
   "apiVersion": "v1",
   ...
 }
```
并不是所有的资源都支持protobuf，尤其是crd。

#### Resource deletion 
资源删除分为两步：1) finalization, and 2) removal。当client第一次删除资源的时候，`.metadata.deletionTimestamp`被设置为当前时间，一旦`.metadata.deletionTimestamp`被设置，外部控制器就可以执行一些回收动作，另外需要注意`.metadata.finalizers`的处理最好是无序的，防止死锁。

#### Resource Versions 
Resource Version是server内部用来表示资源版本的一个字符串，client可以用Resource version来判断资源什么时候被修改了，以及通过resource version来`express data consistency requirements when getting, listing and watching resources`，客户端应该对resource version保持不透明，并且禁止修改，比如，client不能讲resource version视为数字，不能比较大小。

##### ResourceVersion in metadata 
* v1.meta/ObjectMeta，资源的resource version表示最后的修改作用的版本。
* v1.meta/ListMeta，资源collection（例如list的response）表示构建这个collection的时候，资源的resource version。

##### The ResourceVersion Parameter
get/list/watch支持使用resourceVersion参数。

**Get**
|  resourceVersion unset| resourceVersion is 0  | resourceVersion is set but not 0 |
|  ----  | ----  | ---- |
| Most Recent | Any |Not older than|

**List**
list这部分比较复杂，等需要的时候在过来看，