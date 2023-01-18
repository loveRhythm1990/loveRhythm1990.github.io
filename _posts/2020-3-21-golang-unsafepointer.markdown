---
layout:     post
title:      "理解 Golang 中的 unsafe Pointer"
date:       2020-03-21 11:58:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

对于 unsafe.Pointer，在编程过程中用的不多，但是源码中经常遇到，理解 unsafe.Pointer 有助于更好的理解 Golang 的运行机制。

### 定义及使用
在 unsafe 包中，unsafe.Pointer 的定义为 `ArbitratyType`，代表任意类型，代码中的注释说这个类似起占位符的作用，我们就理解成 unsafe.Pointer 是一个可以指向任意类型的指针就可以了。
```go
type Pointer *ArbitraryType
```
因为 unsafe.Pointer 可以指向任意类型，所以任意类型的指针都可以转换为 unsafe.Pointer，通过这个桥梁可以实现任意类型的转换。下面先通过使用来感性了解一下 unsafe.Pointer。

#### unsafe 包中的 api
在 unsafe 包中，提供了一些 api，我们可以直接使用，具体有：
```go 
func Sizeof(x ArbitraryType) uintptr
func Offsetof(x ArbitraryType) uintptr
func Alignof(x ArbitraryType) uintptr

// go1.17 新增的两个 api
func Add(ptr Pointer, len IntegerType) Pointer
func Slice(ptr *ArbitraryType, len IntegerType) []ArbitraryType
```
其中:
* Sizeof: 返回类型 x 所占据的字节数，但不包含 x 所指向的内容的大小。例如，对于一个指针，函数返回的大小为 8 字节（64位机上），一个 slice 的大小则为 SliceHeader 的大小，对于字符串 "ab" 和 "defghi" Sizeof 返回的长度都是 8，也是之返回 StringHeader。
* Offsetof: 返回字段在结构体中的便宜，输入的参数 x 必须是 `A.b` 的格式，否则会报错：`invalid argument: x is not a selector expression`
* Alignof: 返回 m，在对类型 x 进行内存对齐时，其内存地址必须是 m 的倍数，取最大的 m，一般是 4/8 之类的。这个是根据类型而定的，其值跟`reflect.TypeOf(x).Align()` 是相等的。
* Add: 将一个 Pointer 加上一个偏移，在取结构体字段的地址时经常这么操作。
* Slice: 根据提供的 Pointer 和长度，构造一个 Slice。

#### 实现不同类型的相互转换
实现 []byte 和 string 直接的相互转换，通过 reflect.StringHeader、reflect.SliceHeader 进行转换，代码如下。
```go
func main()  {
	bs := []byte{'g', 'o', 'o', 'g', 'l', 'e'}
	fmt.Println(bytesToString(bs))
}
func bytesToString(bs []byte) string{
	sliceHeader := (*reflect.SliceHeader)(unsafe.Pointer(&bs))
	str := reflect.StringHeader{
		Data: sliceHeader.Data,
		Len: sliceHeader.Len,
	}
	return *(*string)(unsafe.Pointer(&str))
}
```

### Facts 以及 Pattern
这个主要是参考文档《[Type-Unsafe Pointers](https://go101.org/article/unsafe.html)》，如果你经常关注足球的话，你会发现 `Facts` 这个单词很有趣（`不想再对阵C罗`，`Facts`）。刚刚的文档中列出了几个 Facts，我们挑选几个语义比较清晰，并且没有冗余的看一下。

* Facts1: unsafe pointers are pointers and uintptr values are integers。 我们都知道 unsafe.Pointer 和 uintptr 可以相互转换，但是对于 Go 运行时来说，前者是指针，在进行 GC 时，会遍历其指向的内容，uintptr 仅仅是个整型，其指向的内容有可能被回收。
* Fact 3: the addresses of some values might change at run time。这个主要是指 Golang 栈中的地址可能会变，
> Golang 中 goroutine 的栈是会动态增长的，具体来说是这样，先分配 2K 的栈内存，2K 不够时重新分配一个 4K 的内存作为栈区，并把之前的 2K内容复制过来，这种情况下，局部变量的地址是会变化的，Golang 运行时会对栈局部变量内的指针重新赋值，但如果是用 uintptr 表示指针的话，Golang是不会重新复制的，这个时候这个 uintptr 指针就无效了。

在官方文档中《[https://pkg.go.dev/unsafe#Pointer](https://pkg.go.dev/unsafe#Pointer)》也列举了一些 unsafe.Pointer 的使用模式，我们也看一下。
* Pattern 1：convert a *T1 value to unsafe Pointer, then convert the unsafe pointer value to *T2，这个主要是实现任意两个类型的相互转换，绕过了 Golang 的类型系统，比如将 float64 转换为 uint64，另外需要注意，在 Golang 中 float64 和 uint64 是类型兼容的，可以相互转换，不过可能会有数据丢失，比如 float64 `4.5` 转换成 int 后变成 `4`。
```go
func Float64bits(f float64) uint64 {
	return *(*uint64)(unsafe.Pointer(&f))
}
```
* Pattern 2: convert unsafe pointer to uintptr, then use the uintptr value. 因为 Golang 中的指针不能进行算数计数，转化成 uintptr 之后，可以进行数据计算，这个经常和 unsafe.Offsetof 联合使用，来计数和访问结构体字段中的地址。另外 `uintptr` 转化为 unsafe.Pointer类型可能是不合法的，（不是一个合法的地址）
* Pattern 4: convert unsafe pointers to uintptr values as arguments of syscall.Syscall calls. 在系统调用中，我们使用 uintprt，这个是安全的，编译器能保证。

总结来看，unsafe的不安全体现在哪里呢？应该是: 1)违反了 Golang 的类型系统，一个类型的变量在取地址后，可以转化为 unsafe.Pointer 类型，这个 unsafe.Pointer 类型又可以转化为任意类型的指针，这个是不安全的。2) 将 uintptr 转化为 unsafe.Pointer 类型时，也可能不是安全的，一方面这个 uintptr 可能不是一个有效的地址，另一个方面就是这个地址可能已经无效了，变量被移动过了。

### 参考

官方文档地址：[Package unsafe](https://golang.org/pkg/unsafe/)

[go 101 Type-Unsafe Pointers](https://go101.org/article/unsafe.html)

[深度解析 Go 语言之 unsafe](https://www.cnblogs.com/qcrao-2018/p/10964692.html)