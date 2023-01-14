---
layout:     post
title:      "gRPC 示例：实现发布订阅模式"
date:       2019-12-16 15:25:00
author:     "decent"
header-img-credit: false
tags:
    - gRPC
---

> 很多协议的标准都是用 gRPC 定义的，对 gRPC 不了解，在理解这些协议的时候也很费劲，做一个简单的 gRPC 入门，主要参考《[官方文档](http://doc.oschina.net/grpc?t=56831)》。

#### gPRC是什么
在 gRPC 框架下，客户端应用可以像调用本地方法一样直接调用另一台不同的机器上服务端应用的方法（RPC 定义），基于此能够更容易地创建分布式应用和服务。与许多 RPC 系统类似，gRPC 也是基于以下理念：定义一个服务，指定其能够被远程调用的方法（包含参数和返回类型）。在服务端实现这个服务接口，并运行一个服务器来处理客户端调用。
gRPC 默认使用 protocol buffers 来进行序列化，这是 Google 开源的一套成熟的结构数据序列化机制（当然也可以使用其他数据格式如 JSON）。

#### gRPC HelloWorld 程序示例
使用 protocol buffers 接口定义语言来定义服务，`service`关键字定义服务，`message`关键字定义自定义结构体
```proto
syntax = "proto3";

package helloworld;
// 服务定义，定义一组服务的方法，及方法的参数，返回值等
service Greeter {
  rpc SayHello (HelloRequest) returns (HelloReply);
}
// 自定义结构体
message HelloRequest {
  // 编码时用编号1代替名字
  string name = 1;
}
// 返回类型定义
message HelloReply {
  string message = 1;
}
```
使用 protoc-gen-go 内置的 gRPC 插件生成 gRPC 代码，hello.pb.go。
```s
protoc --go_out=plugins=grpc:. hello.proto
```
生成的 go 代码中，为自定义结构体定义了 Reset 以及 Get 等方法。为定义的 Service 定义了 `{ServiceName}Client` 以及 `{ServiceName}Server` 接口。对于 `{ServiceName}Client` 接口，有默认的实现（此处生成的默认实现为 `greeterClient`），客户端通过下面方法可以获得此默认实现：
```go
func NewGreeterClient(cc *grpc.ClientConn) GreeterClient {
	return &greeterClient{cc}
}
```
通过此默认实现就可以发起请求。
对于 `{ServiceName}Server` 接口，没有默认实现，需要服务端实现接口后，通过下面方法注册到 gRPC server:
```go
func RegisterGreeterServer(s *grpc.Server, srv GreeterServer) {
	s.RegisterService(&_Greeter_serviceDesc, srv)
}
```

使用 gRPC 实现一个 hello world 的服务端和客户端代码如下，首先是服务端代码：
```go
package main
import (
	"context"
	"log"
	"net"
	"google.golang.org/grpc"
	"../pb"
)
const port = ":50051"

type server struct { // 服务端接口实现，这里直接内嵌 protoc 默认生成的代码，并进行了代码重写（override）
	pb.UnimplementedGreeterServer
}
// 服务端方法实现，覆盖了 pb.UnimplementedGreeterServer 中定义的 SayHello 方法
func (s *server) SayHello(ctx context.Context, in *pb.HelloRequest) (*pb.HelloReply, error) {
	log.Printf("Received: %v", in.GetName())
	return &pb.HelloReply{Message: "Hello " + in.GetName()}, nil
}

func main() {
	// 新建一个端口监听 listen
	listen, err := net.Listen("tcp", port)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}
	// 新建一个 gRPC server，初始 gRPC server 是无状态的
	gRPCServer := grpc.NewServer()
	// 调用 protoc 生成的 RegisterGreeterServer 方法，将我们对于 server 的实现，注册到 gRPC server 中去
	pb.RegisterGreeterServer(gRPCServer, &server{})
	// 启动 gRPC server
	if err := gRPCServer.Serve(listen); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
```
hello world 示例程序的客户端代码：
```go
package main
import (
	"context"
	"log"
	"os"
	"time"
	"google.golang.org/grpc"
	"../pb"
)

const (
	address     = "localhost:50051"
	defaultName = "world"
)

func main() {
	// 建立一个与 gRPC 服务端的 tcp 连接
	conn, err := grpc.Dial(address, grpc.WithInsecure(), grpc.WithBlock())
	if err != nil {
		log.Fatalf("did not connect: %v", err)
	}
	defer conn.Close() 
	// 调用 protoc 生成的 NewGreeterClient 方法，传入 conn，新建一个 gRPC client
	c := pb.NewGreeterClient(conn)

	// Contact the server and print out its response.
	name := defaultName
	if len(os.Args) > 1 {
		name = os.Args[1]
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	// 调用 SayHello 方法
	r, err := c.SayHello(ctx, &pb.HelloRequest{Name: name})
	if err != nil {
		log.Fatalf("could not greet: %v", err)
	}
	log.Printf("Greeting: %s", r.GetMessage())
}
```
#### gRPC 实现发布订阅模式
在《[Moby代码中一个发布订阅模式的实现](https://loverhythm1990.github.io/2021/03/14/pub-sub/》分析了一个 docker 中实现的发布订阅模式。在这个实现中，发布者和订阅者都要持有同一个`pubsub`的实现。订阅者通过`pubsub`订阅时，注册过滤函数，并得到一个 channel，所有事情都会通过这个 channel 返回给订阅者。发布者通过`Publish`发布消息时，会遍历所有的订阅者，如果订阅者对此消息感兴趣，就会向之前的 channel 写入一个数据。

在这里《[advanced-go-programming-book gRPC入门](https://github.com/chai2010/advanced-go-programming-book/blob/master/ch4-rpc/ch4-04-grpc.md)》实现了一个gPRC版的发布订阅模式，这里搬过来分析一下。整个代码的架构如下
![java-javascript](/pics/grpc-pubsub.png){:height="60%" width="60%"}

gRPC server 作为一个发布订阅 Proxy，该 Proxy 持有 Moby 中一个 pubsub 库的实现，gRPC client 即可以作为发布者也可以作为订阅者，我们来看下具体代码实现。首先是定义 proto 文件：
```proto
syntax = "proto3";

package build_in_grpc;

message StrMsg {
  string value = 1;
}

service PubsubService {
  rpc Publish (StrMsg) returns (StrMsg);
  rpc Subscribe (StrMsg) returns (stream StrMsg);
}
```
注意方法 `rpc Subscribe (StrMsg) returns (stream StrMsg)` 中的 `stream` 关键字，表示客户端在发送一次请求之后，服务端流式返回数据。接下来使用 protc 生成 Golang 代码，观察生成的代码。

生成的客户端相关代码如下，从代码中可以看出，生成 gPRC client 的方式没有改变，也是先建立一个 gRPC 连接，然后调用`NewPubsubServiceClient` 返回一个 client。不过这个默认 client 实现的 `Subscribe` 接口的返回值变成一个接口`PubsubService_SubscribeClient` 这个接口有一个 `Recv` 方法，因为服务端是流式返回数据，那客户端就靠这个 `Recv` 方法接收数据了。`Publish` 方法没有变化，跟之前一致。
```go
type PubsubServiceClient interface {
	Publish(ctx context.Context, in *StrMsg, opts ...grpc.CallOption) (*StrMsg, error)
	Subscribe(ctx context.Context, in *StrMsg, opts ...grpc.CallOption) (PubsubService_SubscribeClient, error)
}

type pubsubServiceClient struct {
	cc grpc.ClientConnInterface
}

func NewPubsubServiceClient(cc grpc.ClientConnInterface) PubsubServiceClient {
	return &pubsubServiceClient{cc}
}
// 默认生成的 Publish 方法
func (c *pubsubServiceClient) Publish(ctx context.Context, in *StrMsg, opts ...grpc.CallOption) (*StrMsg, error) {
	out := new(StrMsg)
	err := c.cc.Invoke(ctx, "/build_in_grpc.PubsubService/Publish", in, out, opts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}
// 默认生成的 Subscribe 方法
func (c *pubsubServiceClient) Subscribe(ctx context.Context, in *StrMsg, opts ...grpc.CallOption) (PubsubService_SubscribeClient, error) {
	stream, err := c.cc.NewStream(ctx, &_PubsubService_serviceDesc.Streams[0], "/build_in_grpc.PubsubService/Subscribe", opts...)
	if err != nil {
		return nil, err
	}
	x := &pubsubServiceSubscribeClient{stream}
	if err := x.ClientStream.SendMsg(in); err != nil {
		return nil, err
	}
	if err := x.ClientStream.CloseSend(); err != nil {
		return nil, err
	}
	return x, nil
}

type PubsubService_SubscribeClient interface {
	Recv() (*StrMsg, error)
	grpc.ClientStream
}
```
再看一下 protoc 生成的服务端代码，`Publish` 方法没有改变，不过 `Subscribe` 方法的参数变成了一个 `PubsubService_SubscribeServer` 接口，这个接口有一个 `Send(*StrMsg)` 方法，别慌，这个接口不需要你实现，Server 注册到 gRPC 后，被自动被注入这个接口，我们只需要在 `Subscribe` 接口中使用这个 stream 就可以了。
```go
type PubsubServiceServer interface {
	Publish(context.Context, *StrMsg) (*StrMsg, error)
	Subscribe(*StrMsg, PubsubService_SubscribeServer) error
}
func RegisterPubsubServiceServer(s *grpc.Server, srv PubsubServiceServer) {
	s.RegisterService(&_PubsubService_serviceDesc, srv)
}
type PubsubService_SubscribeServer interface {
	Send(*StrMsg) error
	grpc.ServerStream
}
```
服务端实现如下，也是使用了 docker 的发布订阅组件。客户端调用 `Publish` 发布消息时，就检查所有的订阅者，向对应 channel 发送数据。客户端调用 `Subscribe` 订阅消息时，返回订阅者的 channel，然后就阻塞在该 channel 上，一旦该 channel 有消息就通过 stream 的 `Send` 方法返回给订阅者。

在 server 端的 Subscribe 实现中，通过订阅者发送过来的参数，配置了一个消息过滤器，因为这个程序使用了 Moby 中的订阅发布实现，跟这个库是深度绑定的，参考《[Moby 代码中一个发布订阅模式的实现](https://loverhythm1990.github.io/2021/03/14/pub-sub/)》。
```go
package main

import (
	"context"
	"github.com/atmosphere/grpc/build_in_grpc"
	"github.com/moby/moby/pkg/pubsub"
	"google.golang.org/grpc"
	"log"
	"net"
	"strings"
	"time"
)
// gRPC server 的实现
type PubSubService struct {
	pub *pubsub.Publisher
}
func NewPubsubService() *PubSubService {
	return &PubSubService{
		pub: pubsub.NewPublisher(100* time.Millisecond, 10),
	}
}
// Publish 方法实现，发布者通过 Publish 发布消息
func (p *PubSubService) Publish(ctx context.Context, arg *build_in_grpc.StrMsg) (*build_in_grpc.StrMsg, error){
	// 直接调用 pub 库发布消息
	p.pub.Publish(arg.GetValue())
	return &build_in_grpc.StrMsg{}, nil
}
// Subscribe 具体实现，此方法是供订阅者调用的
func (p *PubSubService) Subscribe(arg *build_in_grpc.StrMsg, steam build_in_grpc.PubsubService_SubscribeServer) error {
	// 调用 Moby 的库添加一个订阅者，并构造了一个过滤器
	ch := p.pub.SubscribeTopic(func(v interface{}) bool {
		if key, ok := v.(string); ok {
			if strings.HasPrefix(key, arg.GetValue()) {
				return true
			}
		}
		return false
	})
	for v := range ch { // 阻塞在channel上，有消息就返回给客户端
		log.Println("send a message to client")
		if err := steam.Send(&build_in_grpc.StrMsg{Value: v.(string)}); err != nil {
			return err
		}
	}
	return nil
}

func main()  {
	// 新建一个 gRPC server
	grpcServer := grpc.NewServer()
	// 注册 server
	build_in_grpc.RegisterPubsubServiceServer(grpcServer, NewPubsubService())
	lis, err := net.Listen("tcp", ":1234")
	if err != nil {
		log.Fatal(err)
	}
	// 启动 server
	grpcServer.Serve(lis)
}
```

`Publish` 客户端的实现跟普通 gRPC 一致，代码就不贴了，`Subscribe` 的客户端如下，因为 `Subscribe` 方法的返回值是一个接口，我们可以通过接口的 `Recv` 方法，不断接收数据，这里的 `Recv` 方法，其功能类似于《[Moby 代码中一个发布订阅模式的实现](https://loverhythm1990.github.io/2021/03/14/pub-sub/)》中，每个订阅者的 channel。

在 Subscribe 接口中，需要一个字符串参数 StrMsg，这个参数起了过滤消息的作用，只接收以该字符为前缀的字符串消息。
```go
func main()  {
	// 新建与 server 的 tcp 连接
	conn, err := grpc.Dial("localhost:1234", grpc.WithInsecure())
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	// 通过 client 发起订阅
	client := build_in_grpc.NewPubsubServiceClient(conn)
	stream, err := client.Subscribe(context.Background(), &build_in_grpc.StrMsg{
		Value: "golang:",
	})
	if err != nil {
		log.Fatal(err)
	}
	for {
		// 阻塞等待消息
		reply, err := stream.Recv()
		if err != nil {
			if err == io.EOF {
				break
			}
			log.Fatal(err)
		}
		fmt.Println(reply.GetValue())
	}
}
```

参考：
[https://developers.google.com/protocol-buffers/docs/proto3](https://developers.google.com/protocol-buffers/docs/proto3)