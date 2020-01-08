---
layout:     post
title:      "关于golang的interface"
subtitle:   " \"学习golang中的一些类型\""
date:       2020-1-3 17:47:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

> 了解一些golang中的interface，通过阅读golang的源代码理清一下golang中的类型，基于的golang版本为1.12.1。本文主要参考[go语言核心编程-李文塔]

interface分为两种：代表任意类型的eface，以及含有接口方法的iface，这部分内容涉及到很多golang中的类型，通过对这部分内容的总结，对golang的类型的内部实现能有较深的理解。网上对这部分内容有较多的介绍（比如参考链接部分），本文属于重复总结，自我学习。

### 非空接口eface
非空接口就是含有方法的接口，其定义位于`src/runtime/runtime2.go`
```go
type iface struct {
	tab  *itab            
	data unsafe.Pointer 
}
```
其中`data`是指向数据的指针，什么数据呢？就是接口绑定的动态类型的数据，接口绑定的实例是原实例的一个副本，接口的初始化也是一种值拷贝（如果用指针初始化的interface，那么这个data就是指向那个指针的副本）。`itab`结构体定义如下，主要存放接口自身类型（interfacetype）、绑定的实例类型、实例相关的函数指针。具体如下：
* inner执行interface本身的类型interfacetype
* _type是指向接口存放的具体类型原信息的指针，iface里的data指针指向的就是该类型的值，这个是类型信息，data是值信息。
* hash是具体的类型的Hash值，_type里面也有hash，这里冗余存放主要是为了**接口断言**或者**接口查询**时快速访问。
* func是一个函数指针，**指向的是具体类型的方法**，这里虽然只有一个元素，实际上指针数组的大小时可变的，编译器负责填充，运行时使用底层指针进行访问，不会受struct类型越界检查的约束。

```go
type itab struct {
	inter *interfacetype
	_type *_type
	hash  uint32 // copy of _type.hash. Used for type switches.
	_     [4]byte
	fun   [1]uintptr // variable sized. fun[0]==0 means _type does not implement inter.
}
```
itab这个数据结构是非空接口实现动态调用的基础，itab的信息被编译器和链接器保存了下来，存放在**可执行文件的只读存储段(.rodata)**中，itab存放在静态分配的存储空间中，不受GC的限制，其内存不会被回收。

`_type`是go语言类型信息的通用结构，所有类型都内嵌了一个_type结构，
```go
type _type struct {
	size       uintptr //大小
	ptrdata    uintptr // size of memory prefix holding all pointers
	hash       uint32  //类型的hash
	tflag      tflag   //类型的特征标记
	align      uint8   //_type作为整体变量存放时的对齐字节数
	fieldalign uint8   //当前结构字段的对齐字节数
	kind       uint8   //基础类型枚举值，和反射中的Kind一致，kind决定了如何解析该类型
	alg        *typeAlg
	// gcdata stores the GC type data for the garbage collector.
	// If the KindGCProg bit is set in kind, gcdata is a GC program.
	// Otherwise it is a ptrmask bitmap. See mbitmap.go for details.
	gcdata    *byte    // GC相关信息
	str       nameOff
	ptrToThis typeOff
}
```
其中`alg`指向一个函数指针表，该表有两个函数，一个是计算类型Hash函数，另一个是比较两个类型是否相同的equal函数。`nameOff`用来表示类型名称字符串在**编译后的二进制文件中**某个section的偏移量，由链接器负责填充。`typeOff`用来表示类型原信息的指针在编译后的二进制文件中某个section的偏移量，由链接器负责填充。_type里面的nameOff和typeOff最终是由链接器负责确定和填充的，它们都是一个偏移量(offset)，类型的名称和类型元信息实际上存放在连接后可执行文件的某个段(section)里，这两个值是相对于段内的偏移量，运行时提供两个转换查找函数 `src/runtime/type.go`。
```go
func resolveNameOff(ptrInModule unsafe.Pointer, off nameOff)
func resolveTypeOff(ptrInModule unsafe.Pointer, off typeOff)
```
> go语言类型元信息最初由编译器负责构建，并以表的形式存放在编译后的对象文件中，再由链接器在链接时进行段的合并、符号重定向（填充某些值），这些类型信息在接口动态调用和反射中被调用。

### 空接口
空接口不包含方法，是一个万能的类型，任何类型的变量都可以赋值给空接口，其定义为：
```
type eface struct {
	_type *_type
	data  unsafe.Pointer
}
```
空接口保留了具体实例的类型和值拷贝，由于空接口自身没有方法集，所以空接口变量实例化后的真正用途不是接口方法的动态调用。空接口在go语言中的**真正意义是支持多态**，有如下几种方式使用了空接口（将空接口类型还原）：
* 通过接口类型断言
* 通过接口类型查询
* 通过反射

### go类型元信息
首先看`interfacetype`，所在文件为`src/runtime/type.go`，`pkgpath`表示接口所属包的名字信息，name内存放的不仅有名称，还有描述信息，
```go
//接口方法元信息
type imethod struct {
	name nameOff //方法名在编译后的 section 里面的偏移量
	ityp typeOff //方法类型在编译后的 section 里面的偏移量
}

type interfacetype struct {
	typ     _type   //类型通用部分
	pkgpath name
	mhdr    []imethod  //接口的方法
}

// name is an encoded type name with optional extra data.
// See reflect/type.go for details.
type name struct {
	bytes *byte
}
```

### 参考

[GO Interface 实现 https://wudaijun.com/2018/01/go-interface-implement/](https://wudaijun.com/2018/01/go-interface-implement/)

[go语言核心编程-李文塔]





