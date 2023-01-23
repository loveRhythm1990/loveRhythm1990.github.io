---
layout:     post
title:      "gRPC Protobuf 语法介绍"
subtitle:   ""
date:       2019-12-17 15:25:00
author:     "decent"
header-img-credit: false
tags:
    - gRPC
---

在《[gRPC 示例：实现发布订阅模式](https://loverhythm1990.github.io/2019/12/15/grpc-brief/)》中，介绍了 gRPC 的一些使用，在本文中希望介绍一些 protobuf 的一些语法，希望能覆盖工作中的一些 case，做到即查即用，同时对 protobuf 有进一步的认识。

鉴于之前做 K8s 存储比较多，这里定了个小目标，就是理解 `csi.proto` 中的语法，csi 项目的地址为 [container-storage-interface/spec](https://github.com/container-storage-interface/spec)，同时参考文档给出了官方文档的链接，写文档的过程就是一个从0到1的过程。

#### 关键字解释
###### package 与 option go_package
`package` 用来防止两个 proto 文件之间 message 定义冲突，在 Golang 中，`package`指定的内容，就是生成的 `xxx.pb.go` 代码中的 package 内容，如果额外指定了 `option go_package` 选项，那么`go_package` 所指的内容是 `xxx.pb.go` 代码中的 package 内容，由此看来，我们指定其中一个就可以了。如果是用 gRPC，使用 `option go_package` 就好了，其指定的路径是相对当前路径而言的，比如，我们在当前目录有一个 proto 文件，并使用下面命令生成 gRPC 代码，并且 `option go_package = "github.com/testgrpc"`，那么会在当前目录创建目录 `github.com/testgrpc`。
```s
protoc --go_out=plugins=grpc:. hello.proto
```

###### enum 枚举类型
概括来讲在 Golang 中，会为 enum 生成一个新的自定义类型，并定义几个这种类型的常量；这个跟我们在代码中的使用习惯类似，比如我们会将错误、消息等定义为一个新的类型，并预先定义几个常量等（`type MessageType string`）。假设我们有如下的 proto 定义，我们看看 protoc 帮我们生成的 Golang 代码。
```protobuf
syntax = "proto3";
package tutorial;
option go_package = ".;tutorial";

// 很多示例都把 enum 内嵌在 message 中定义，内嵌定义的好处是，写在不同 message 中的 enum 可以重名，生成代码的时候，会自动帮我们命名成不重名的。
enum PhoneType {
  MOBILE = 0;
  HOME = 1;
  WORK = 2;
}

message PhoneNumber {
  string number = 1;
  PhoneType type = 2;
}
```
生成的代码如下，首先定义了一个 `PhoneType` 类型（对应 proto 中的 enum PhoneType），底层类型为 int32，然后定义了 `PhoneType `类型的几个常量，然后通过 map 对常量以及字符串进行了一一映射。
```go
type PhoneType int32
const (
	PhoneType_MOBILE PhoneType = 0
	PhoneType_HOME   PhoneType = 1
	PhoneType_WORK   PhoneType = 2
)
// Enum value maps for PhoneType.
var (
	PhoneType_name = map[int32]string{
		0: "MOBILE",
		1: "HOME",
		2: "WORK",
	}
	PhoneType_value = map[string]int32{
		"MOBILE": 0,
		"HOME":   1,
		"WORK":   2,
	}
)
// 省略了 PhoneType 的其他方法，...
func (x PhoneType) String() string {
	return protoimpl.X.EnumStringOf(x.Descriptor(), protoreflect.EnumNumber(x))
}

type PhoneNumber struct {
	state         protoimpl.MessageState
	sizeCache     protoimpl.SizeCache
	unknownFields protoimpl.UnknownFields

	Number string    `protobuf:"bytes,1,opt,name=number,proto3" json:"number,omitempty"`
	Type   PhoneType `protobuf:"varint,2,opt,name=type,proto3,enum=tutorial.PhoneType" json:"type,omitempty"`
}
```
###### oneof
oneof 用来指定设置多个字段中的一个，类似于 c 语言中 union。在生成的 Golang 代码中，为每个 union 字段分配生成了一种类型，而 message 中的对应字段，则被定义成了接口，每个 union 都实现了接口中的方法（一种类型安全机制，并没有将 message 定义为可代表任意类型的 `interface{}`，而是需要实现特定方法）。通过 oneof 可以实现多态，但是感觉不是很优雅。具体例子如下：
```protobuf
message OneofMessage {
  oneof test_oneof {
    string name = 4;
    int64 value = 9;
  }
}
```
会分别给 `name` 以及 `value` 生成两种不同的类型，即 `OnofMessage_Name `和 `OneofMessage_Value`，这两种类型都实现了 `isOneofMessage_TestOneof` 接口， 我们使用这两种类型来初始化 OneofMessage 就可以了，不过得在具体使用的时候得做类型断言，不然不知道是什么类型。
```go
type OneofMessage_Name struct {
	Name string `protobuf:"bytes,4,opt,name=name,proto3,oneof"`
}
type OneofMessage_Value struct {
	Value int64 `protobuf:"varint,9,opt,name=value,proto3,oneof"`
}
type OneofMessage struct {
        state         protoimpl.MessageState
        sizeCache     protoimpl.SizeCache
        unknownFields protoimpl.UnknownFields

        // Types that are assignable to TestOneof:
        //      *OneofMessage_Name
        //      *OneofMessage_Value
        TestOneof isOneofMessage_TestOneof `protobuf_oneof:"test_oneof"`
}
type isOneofMessage_TestOneof interface {
        isOneofMessage_TestOneof()
}
```

###### map
map 能直接翻译成 Golang 中的 map，这个不用多介绍了。
```protobuf
message MapMessage {
  map<int64,string> values = 1;
}
```
生成的代码如下：
```go
type MapMessage struct {
	state         protoimpl.MessageState
	sizeCache     protoimpl.SizeCache
	unknownFields protoimpl.UnknownFields

	Values map[int64]string `protobuf:"bytes,1,rep,name=values,proto3" json:"values,omitempty" protobuf_key:"varint,1,opt,name=key,proto3" protobuf_val:"bytes,2,opt,name=value,proto3"`
}
```

###### gogo库
虽然官方库 [golang/protobuf](https://github.com/golang/protobuf) 提供了对 Protobuf 的支持，但是使用最多还是第三方实现的库[gogo/protobuf](https://github.com/gogo/protobuf)。gogo 库基于官方库开发，增加了很多的功能，包括：

* 快速的序列化和反序列化
* 更规范的 Go 数据结构
* goprotobuf 兼容
* 可选择的产生一些辅助方法，减少使用中的代码输入
* 可以选择产生测试代码和benchmark代码
* 其它序列化格式

gogo库的安装方式如下，下面命令会在 ${GOPATH}/bin 目录下面下载 `protoc-gen-gofast` 二进制文件。
```s
go get github.com/gogo/protobuf/protoc-gen-gofast
```
使用 gogo 生成 gRPC 代码的方式如下，使用 gogo 库生成的代码看上去更简洁一些。
```s
protoc --gofast_out=plugins=grpc:. my.proto
```

###### extensions 扩展 
扩展是指扩展一个已经存在的 message，首先通过 `extensions` 关键字声明允许扩展的标号，其他 proto 文件可以 `import` 这个文件，并使用`extend` 关键字进行扩展。比如
```protobuf
message Foo {
  // ...
  extensions 100 to 199;
}
```
这个例子表明：在 message Foo中，范围`[100,199]`之内的字段标识号被保留为扩展用。现在，其他人就可以在他们自己的 .proto 文件中添加新字段到 Foo 里了，但是添加的字段标识号要在指定的范围内——例如：
```protobuf
extend Foo {
  optional int32 bar = 126;
}
```
这个例子表明：消息 Foo 现在有一个名为 bar 的 optional int32字段。

#### 参考

[官方 Language Guide (proto 3)](https://protobuf.dev/programming-guides/proto3/)

[Language Guide (proto3)](https://developers.google.com/protocol-buffers/docs/proto3)

[Protocol Buffer Basics: Go](https://developers.google.com/protocol-buffers/docs/gotutorial)

[Protobuf 终极教程](https://colobu.com/2019/10/03/protobuf-ultimate-tutorial-in-go/)
