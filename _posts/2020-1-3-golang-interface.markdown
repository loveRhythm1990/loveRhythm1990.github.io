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

### 非空接口iface
非空接口就是含有方法的接口，其定义位于`src/runtime/runtime2.go`
```go
type iface struct {
	tab  *itab            
	data unsafe.Pointer 
}
```
其中`data`是指向数据的指针，什么数据呢？就是接口绑定的动态类型的数据，接口绑定的实例是原实例的一个副本，接口的初始化也是一种值拷贝（如果用指针初始化的interface，那么这个data就是指向那个指针的副本）。`itab`结构体定义如下，主要存放接口自身类型（interfacetype）、绑定的实例类型、实例相关的函数指针。具体如下：
* inner是interface本身的类型interfacetype
* _type是指向接口存放的具体类型原信息的指针，iface里的data指针指向的就是该类型的值，这个是类型信息，data是值信息。
* hash是具体的类型的Hash值，_type里面也有hash，这里冗余存放主要是为了**接口断言**或者**接口查询**时快速访问。
* func是一个函数指针，**指向的是具体类型的方法**，这里虽然只有一个元素，实际上指针数组的大小时可变的，编译器负责填充，运行时使用底层指针进行访问，不会受struct类型越界检查的约束。

```go
type itab struct {
	inter *interfacetype   // 接口自身这种类型
	_type *_type           // 动态示例的类型
	hash  uint32     // copy of _type.hash. Used for type switches.
	_     [4]byte
	fun   [1]uintptr // variable sized. fun[0]==0 means _type does not implement inter.
}
```
itab这个数据结构是非空接口实现动态调用的基础，itab的信息被编译器和链接器保存了下来，存放在**可执行文件的只读存储段(.rodata)**中，itab存放在静态分配的存储空间中，不受GC的限制，其内存不会被回收。

`_type`是go语言类型信息的通用结构，里面包含了GC、反射等需要的细节，它决定data应该如何解释和操作，其定义在文件`src/runtime/type.go`，所有类型都内嵌了一个_type结构，
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
其中`alg`指向一个函数指针表，该表有两个函数，一个是计算类型Hash函数，另一个是比较两个类型是否相同的equal函数。`nameOff`用来表示*类型名称字符串*在**编译后的二进制文件中**某个section的偏移量，由链接器负责填充。`typeOff`用来表示*类型原信息的指针*在编译后的二进制文件中某个section的偏移量，由链接器负责填充。_type里面的nameOff和typeOff最终是由链接器负责确定和填充的，它们都是一个偏移量(offset)，类型的名称和类型元信息实际上存放在连接后可执行文件的某个段(section)里，这两个值是相对于段内的偏移量，运行时提供两个转换查找函数。
```go
func resolveNameOff(ptrInModule unsafe.Pointer, off nameOff)
func resolveTypeOff(ptrInModule unsafe.Pointer, off typeOff)
```
> go语言类型元信息最初由编译器负责构建，并以表的形式存放在编译后的对象文件中，再由链接器在链接时进行段的合并、符号重定向（填充某些值），这些类型信息在接口动态调用和反射中被调用。

##### golang中类型拓展
各个类型所需要的类型信息是不一样的，比如chan，除了chan自身，还需要描述其元素类型，而map则需要key信息和value信息等。在`src/runtime/type.go`中还包含了下面类型等其他类型的信息：
```go

// 非空接口类型，接口定义，包路径等。
type interfacetype struct {
   typ     _type
   pkgpath name
   mhdr    []imethod      // 接口方法声明列表，按字典序排序
}

//接口方法元信息
type imethod struct {
	name nameOff //方法名在编译后的 section 里面的偏移量
	ityp typeOff //方法类型在编译后的 section 里面的偏移量
}

// ptrType represents a pointer type.
type ptrType struct {
   typ     _type   // 指针类型 
   elem  *_type    // 指针所指向的元素类型
}
type chantype struct {
    typ  _type        // channel类型
    elem *_type       // channel元素类型
    dir  uintptr
}
type arraytype struct {
	typ   _type
	elem  *_type     //数组元素的类型
	slice *_type
	len   uintptr
}
type maptype struct {
    typ           _type
    key           *_type  // key类型
    elem          *_type  // element类型
    bucket        *_type // internal type representing a hash bucket
    hmap          *_type // internal type representing a hmap
    keysize       uint8  // size of key slot
    indirectkey   bool   // store ptr to key instead of key itself
    valuesize     uint8  // size of value slot
    indirectvalue bool   // store ptr to value instead of value itself
    bucketsize    uint16 // size of bucket
    reflexivekey  bool   // true if k==k for all keys
    needkeyupdate bool   // true if we need to update key on an overwrite
}
```
这些类型信息的第一个字段都是_type（类型本身的信息，如类型hash/size/kind/偏移等），接下来是一堆类型需要的其它详细信息（如子类型信息），这样在进行类型相关操作时，可通过`typ *_type`即可表示所有的类型，然后再通过`_type.kind`可解析出具体类型，最后通过地址转换即可得到类型完整的_type架构，参考reflect.Type.Elem()函数：
```go
// reflect.rtype结构体定义和runtime._type一致  type.kind定义也一致(为了分包而重复定义)
// Elem()获取rtype中的元素类型，只针对复合类型(Array, Chan, Map, Ptr, Slice)有效
func (t *rtype) Elem() Type {
   switch t.Kind() {
   case Array:
      tt := (*arrayType)(unsafe.Pointer(t))
      return toType(tt.elem)   //对于数组类型来说，tt.elem表示的是其元素的类型
   case Chan:
      tt := (*chanType)(unsafe.Pointer(t))
      return toType(tt.elem)
   case Map:
      // 对Map来讲，Elem()得到的是其Value类型
      // 可通过rtype.Key()得到Key类型
      tt := (*mapType)(unsafe.Pointer(t))
      return toType(tt.elem)
   case Ptr:
      tt := (*ptrType)(unsafe.Pointer(t))
      return toType(tt.elem)
   case Slice:
      tt := (*sliceType)(unsafe.Pointer(t))
      return toType(tt.elem)
   }
   // 对不包含elem元素的类型调用Elem会panic
   panic("reflect: Elem of invalid type")
}
```

### 空接口eface
空接口不包含方法，是一个万能的类型，任何类型的变量都可以赋值给空接口，其定义为：
```go
type eface struct {
	_type *_type
	data  unsafe.Pointer
}
```
空接口保留了具体实例的类型和值拷贝，由于空接口自身没有方法集，所以空接口变量实例化后的真正用途不是接口方法的动态调用。空接口在go语言中的**真正意义是支持多态**，有如下几种方式使用了空接口（将空接口类型还原）：
* 通过接口类型断言
* 通过接口类型查询
* 通过反射

对于空接口类型，没有自身的类型信息，只保存了具体实例的类型，难道是因为没有方法集？

### 参考

[GO Interface 实现 https://wudaijun.com/2018/01/go-interface-implement/](https://wudaijun.com/2018/01/go-interface-implement/)

[go语言核心编程-李文塔]





