---
layout:     post
title:      "流式 gRPC 示例"
date:       2021-12-18 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - gRPC
---

**目录**
- [总结](#总结)
- [服务端流式 gRPC](#服务端流式-grpc)
- [客户端流式 gRPC](#客户端流式-grpc)
- [双向 gRPC](#双向-grpc)

流式 gRPC 示例及理解，参考《gRPC 与云原生应用开发》，下面有服务端流式 gRPC、客户端 gRPC、双向 gRPC 的示例。

### 总结
对于一个服务方法（在 service 中定义的方法）
* 如果返回值带着 `stream` 表示服务端以流的形式返回数据，客户端要在 for 循环里不断的通过 Recv 方法接收数据。**什么时候返回完数据由服务端决定**（当然客户端也可以随时跳出 for 循环）
* 如果参数带着 `stream` 表示客户端以流的形式发送请求，**什么时候结束由客户端说了算**，也就是通过 `CloseAndRecv` 方法通知服务端的，服务端收到这个方法后，将处理结果一并返回给客户端。
* 双向流方法的参数和返回值都带着 stream 关键字，**什么时候结束由客户端说了算**，客户端调用 stream 的 `CloseSend` 来结束流的发送。

### 服务端流式 gRPC
以下面 proto 文件为例，假设有一个订单管理系统，服务端有很多订单信息，客户端搜索订单，参数为一个前缀，搜索以包含这个前缀的所有订单。
```protobuf
syntax="proto3";

import "google/protobuf/wrappers.proto"; // 导入这个包，从而使用常见的类型，如 StringValue

package ecommerce;

service OrderManagement {
    // 检索订单的远程方法
    // 参数为前缀，并以 stream 的形式返回 order
  rpc searchOrder(google.protobuf.StringValue) returns(stream Order);
}

message Order {  // 定义 Order 类型
  string id=1;
  repeated string items=2;
  string description=3;
  float price=4;
  string destination=5;
}
```
服务端实现如下，因为是流式返回，服务端方法有个额外的参数 `OrderManagement_SearchOrderServer`，其为流的引用对象，可以写入多个响应，每次服务端想返回数据时，直接调用这个参数的 `Send` 方法就可以了，比如下的 `err := srv.Send(&v)`，`Send` 的参数为 Order，也就是 protobuf 中定义的方法的返回值。`OrderManagement_SearchOrderServer`的具体实现不需要我们关注，我们只需要调用 Send 方法，并传入 Order 参数就可以了。

```go
package main

import (
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	wrapper "google.golang.org/protobuf/types/known/wrapperspb"
	"log"
	"net"
	"strings"
	"time"
)
const port = ":50051"
type server struct {
	orderMap map[string]pb.Order // 用一个 map 来缓存订单
}

func (s *server) SearchOrder(req *wrapper.StringValue, srv pb.OrderManagement_SearchOrderServer) error {
	for k, v := range s.orderMap {
        prefix := req.Value
		if strings.HasPrefix(k, prefix) {	// 查找前缀，每个 5 秒返回数据
			err := srv.Send(&v)	// 通过流发送订单
			if err != nil {
				return err
			}
        }
		time.Sleep(time.Second * 5)	// 每隔 5 秒返回一个订单
	}
	return nil
}

func main() {
	l, err := net.Listen("tcp", port)
	if err != nil {
		log.Fatalf("lister err: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterOrderManagementServer(s, newOrderServer())

	if err := s.Serve(l); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}

func newOrderServer() *server {
	return &server{
		orderMap: map[string]pb.Order{
			"o1": {Id:"oo1"},
			"o2": {Id:"oo2"},
			"o3": {Id:"oo3"},
		},
	}
}

```
客户端实现如下，客户端实现也是多了一个返回值 `searchStream`，调用 `SearchOrder` 方法的时候，不是直接返回结果，而是返回一个 stream，可通过这个 stream 的 Recv 方法不断接收数据，等到接收到 `io.EOF` 错误时，表示服务端中断了连接，所有数据都发送完了。
```go
package main

import (
	"context"
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	wrapper "google.golang.org/protobuf/types/known/wrapperspb"
	"io"
	"log"
)

const (
	address = "localhost:50051"
)

func main() {
	conn, err := grpc.Dial(address, grpc.WithInsecure())
	if err != nil {
		log.Fatalf("dial err: %v", err)
	}
	defer conn.Close()

	ctx := context.TODO()
	orderMgtClient := pb.NewOrderManagementClient(conn)
	searchStream, err := orderMgtClient.SearchOrder(ctx, &wrapper.StringValue{Value: "o"})
	if err != nil {
		log.Fatal(err)
	}

	for {  //通过一个 for 循环不断的接收数据
		order, err := searchStream.Recv()
		if err == io.EOF {
			break
		}
		log.Printf("result: %v", order)
	}
}
```

### 客户端流式 gRPC
在客户端流式 gRPC 中，客户端多次发送请求给服务端，服务端依次处理请求，等客户端觉得自己请求都发送完了，通过 `CloseAndRecv` 方法通知服务端，服务端在收到客户端发送完了的消息后，将结果打包返回给客户端。

以下面业务逻辑为例：客户端以流的形式向服务端发送 `GetOrder` 请求，发送好几个，服务端收到一个请求就处理一个请求，但是并不直接返回，而且等着客户端发送结束，客户端结束后，服务端打包处理结果，一并返回。

proto 定义如下
```protobuf
syntax="proto3";

import "google/protobuf/wrappers.proto"; // 导入这个包，从而使用常见的类型，如 StringValue

package ecommerce;

service OrderManagement {
  // 客户端以流的形式获取订单，每隔 1s 发送一个请求
  // 服务端打包返回
  rpc getOrder(stream google.protobuf.StringValue) returns(OrderList);
}

message Order {  // 定义 Order 类型
  string id=1;
  repeated string items=2;
  string description=3;
  float price=4;
  string destination=5;
}

message OrderList {
  repeated Order orderList=1;
}
```
服务端代码如下：
```go
package main

import (
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	"io"
	"log"
	"net"
)

const port = ":50051"

type server struct {
	orderMap map[string]pb.Order
}

func (s *server) GetOrder(srv pb.OrderManagement_GetOrderServer) error {
	res := pb.OrderList{}
	for {
		orderId, err := srv.Recv()
		if err == io.EOF {
			return srv.SendAndClose(&res)	// 完成读取流订单，所有处理结果打包返回
        }
		order, exist := s.orderMap[orderId.Value] // 处理一个请求
		if exist {
			res.OrderList = append(res.OrderList, &order)
		}
	}
	return nil
}

func main() {
	l, err := net.Listen("tcp", port)
	if err != nil {
		log.Fatalf("lister err: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterOrderManagementServer(s, newOrderServer())

	if err := s.Serve(l); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}

func newOrderServer() *server {
	return &server{
		orderMap: map[string]pb.Order{
			"o1": {Id:"oo1"},
			"o2": {Id:"oo2"},
			"o3": {Id:"oo3"},
		},
	}
}
```
客户端代码如下，在流式客户端的实现中，就是多次调用 stream 的 `Send` 方法发送请求。
```go
package main

import (
	"context"
	"fmt"
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	wrapperspb "google.golang.org/protobuf/types/known/wrapperspb"
	"log"
)

const (
	address = "localhost:50051"
)

func main() {
	conn, err := grpc.Dial(address, grpc.WithInsecure())
	if err != nil {
		log.Fatalf("dial err: %v", err)
	}
	defer conn.Close()

	ctx := context.TODO()
	orderMgtClient := pb.NewOrderManagementClient(conn)
	stream, err := orderMgtClient.GetOrder(ctx)	// getOrder 并不是直接返回结果，而是返回一个 stream
	if err != nil {
		log.Fatalf("getOrder err: %v", err)
	}
	if err := stream.Send(&wrapperspb.StringValue{Value: "o1"}); err != nil {	// 发送一次请求
		log.Fatalf("send err: %v", err)
    }
    
	if err := stream.Send(&wrapperspb.StringValue{Value: "o2"}); err != nil {	// 再发送一次请求
		log.Fatalf("send err: %v", err)
	}
	res, err := stream.CloseAndRecv()	// 好了，都发送完了，请求打包的结果
	if err != nil {
		log.Fatalf("recv err: %v", err)
	}
	fmt.Println(res.OrderList)
}
```
### 双向 gRPC
双向流就是服务方法的参数以及返回值都带着 stream 关键字。现在假设有这样的业务，客户端通过流的形式发送 getOrder 请求，服务端每收到一个请求就进行一次返回，也是通过流的形式返回。客户端是这样的，它先通过多次调用 `Send` 发送请求，然后在一个独立的 goroutine 里接收服务端的返回结果，也就是客户端的**发送和接收是分离的，在不同的 goroutine 中**。

**流的结束是由客户端决定的**，其调用 `CloseSend` 方法标识自己已经发送完数据了。在客户端调用 `GetOrder` 方法时，同 `客户端流式 gRPC` 一样，这个方法并不是直接返回结果，而是返回一个 stream，但是这个 stream 可以读，也可以写，而 `客户端流式gRPC` 中的 stream 只能写。

结束的流式如下：
1. `Send` goroutine 调用 `CloseSend` 给服务端。
2. 服务端收到 `io.EOF` 错误，调用 `return nil` 直接返回。
3. `Recv` goroutine 收到 `io.EOF` 错误，结束接收。

具体实现如下。proto 定义
```protobuf
syntax="proto3";

import "google/protobuf/wrappers.proto"; // 导入这个包，从而使用常见的类型，如 StringValue

package ecommerce;

service OrderManagement {
  // 客户端以流的形式获取订单，每发送一个，服务端返回一个
  rpc getOrder(stream google.protobuf.StringValue) returns(stream Order);
}

message Order {  // 定义 Order 类型
  string id=1;
  repeated string items=2;
  string description=3;
  float price=4;
  string destination=5;
}
```
服务端代码实现
```go
package main

import (
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	"io"
	"log"
	"net"
)
const port = ":50051"
type server struct {
	orderMap map[string]pb.Order
}
func (s *server) GetOrder(srv pb.OrderManagement_GetOrderServer) error {
	for {
		orderId, err := srv.Recv()
		if err == io.EOF {
			return nil // 处理完了，返回，把 io.EOF 当做正常结束的标志
		}
		if err != nil {
			log.Fatal(err)
		}
		order, exist := s.orderMap[orderId.Value]
		if exist {
			srv.Send(&order) 	// 立即返回这个结果
		}
	}
	return nil
}

func main() {
	l, err := net.Listen("tcp", port)
	if err != nil {
		log.Fatalf("lister err: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterOrderManagementServer(s, newOrderServer())

	if err := s.Serve(l); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}

func newOrderServer() *server {
	return &server{
		orderMap: map[string]pb.Order{
			"o1": {Id:"oo1"},
			"o2": {Id:"oo2"},
			"o3": {Id:"oo3"},
		},
	}
}
```
客户端代码实现
```go
package main

import (
	"context"
	"fmt"
	pb "github.com/atmosphere/grpc/protobuf"
	"google.golang.org/grpc"
	wrapperspb "google.golang.org/protobuf/types/known/wrapperspb"
	"io"
	"log"
	"time"
)

const (
	address = "localhost:50051"
)

func main() {
	conn, err := grpc.Dial(address, grpc.WithInsecure())
	if err != nil {
		log.Fatalf("dial err: %v", err)
	}
	defer conn.Close()
	ctx := context.TODO()
	orderMgtClient := pb.NewOrderManagementClient(conn)
    // 通过 GetOrder 返回一个 stream，这个 stream 可以进行读写
    stream, err := orderMgtClient.GetOrder(ctx)
	if err != nil {
		log.Fatalf("getOrder err: %v", err)
	}
    // 先启动一个 goroutine 来处理接收请求，没问题的
	go asycRecv(stream)
    // 发送一个 get 请求
	if err := stream.Send(&wrapperspb.StringValue{Value: "o1"}); err != nil {
		log.Fatalf("send err: %v", err)
    }
    // 等待 5s 再发送一个请求
	time.Sleep(time.Second * 5)
	if err := stream.Send(&wrapperspb.StringValue{Value: "o2"}); err != nil {
		log.Fatalf("send err: %v", err)
	}
    // 发送完了
	if err := stream.CloseSend(); err != nil {
		log.Fatalf("close sent: %v", err)
	}
	time.Sleep(time.Minute)
}

func asycRecv(stream pb.OrderManagement_GetOrderClient)  {
	for {
		order, err := stream.Recv()
		if err == io.EOF { // 收到 EOF 错误，表示服务端处理完成
			break
		}
		if err != nil {
			log.Fatalf("receive err: %v", err)
		}
		fmt.Printf("reveive: %v\n", order)
	}
}
```