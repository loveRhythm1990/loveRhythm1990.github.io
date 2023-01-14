---
layout:     post
title:      "Golang unsafe pointer"
date:       2020-03-21 11:58:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

官方文档地址：[Package unsafe](https://golang.org/pkg/unsafe/)

先看一下Pointer类型的定义，在Pointer类型的定义上，有一大段注释，这段注释golang文档中也有：
```go
// ArbitraryType is here for the purposes of documentation only and is not actually
// part of the unsafe package. It represents the type of an arbitrary Go expression.
type ArbitraryType int
type Pointer *ArbitraryType

// uintptr is an integer type that is large enough to hold the bit pattern of
// any pointer.
type uintptr uintptr
```

unsafe包中包含一些类型不安全的操作，在go官方文档中[Package unsafe](https://golang.org/pkg/unsafe/)对类型`Pointer`有这样的叙述： Pointer代表任意类型的指针，对于Pointer类型有4中特别的操作：

* 任何类型的指针可以转化为unsafe.Pointer类型 
* unsafe.Pointer类型可以转化为任何类型的指针
* uintptr可以被转化为unsafe.Pointer类型
* unsafe.Pointer类型可以转化为uintptr类型

不同类型的指针是不可以直接转化的，通过unsafe.Pointer类型可以做到这点，比如官方的例子，将一个float64类型，解释为uint64类型（*通过这种方式，我们可以查看float64在内存中是怎么表示的，只需要转化为uint64之后，，按照16进制方式打印出来就可以*），
```go
	func Float64bits(f float64) uint64 {
		// 1. 取float64地址
		// 2. 转化为unsafe.Pointer类型
		// 3. 将unsafe.Pointer类型转化为*uint64类型
		// 4. 取*uint64类型指向的内容
		return *(*uint64)(unsafe.Pointer(&f))
	}
```
在上面代码中，不能直接将\*float64类型转化为*uint64类型，类型不匹配

`uintptr`转化为unsafe.Pointer类型往往是不合法的，golang官方文档中列出了一些特殊场景，在这些场景下，可以将uintptr类型转化为unsafe.Pointer。包括：

* 调用系统调用`syscall.Syscall`时，将Pointer类型转化为uintptr类型，（这个syscall.Syscall要求的参数是uintptr类型，底层会在需要时将其转化为指针）
* 将`reflect.SliceHeader`以及`reflect.StringHeader`的Data字段转化为unsafe.Pointer类型
这两种类型的定义是`src/reflact/value.go`：
```go
type SliceHeader struct {
	Data uintptr   // uintptr类型
	Len  int
	Cap  int
}
type StringHeader struct {
	Data uintptr
	Len  int
}
```
其他规则，还没有详细研究。另外还有一种情况，在将unsafe.Pointer转化为uintptr类型后，后者会变得无效：

> golang中goroutine的栈是会动态增长的，具体来说是这样，先分配2K的栈内存，2K不够时重新分配一个4K的内存作为栈区，并把之前的2K内容复制过来，这种情况下，局部变量的地址是会变化的，golang运行时会对栈局部变量内的指针重新赋值，但如果是用uintptr表示指针的话，golang是不会重新复制的，这个时候这个uintptr指针就无效了。参考下面代码：

```go
type X struct {
	a bool
	b int16
	c []int
}
func unsafePointer() {
	x := X{}
	//先获取局部变量x的地址，然后获取field b相对与整个struct的偏移，相加得到
	// field b的地址，然后通过field b的地址修改b的内容
	// 这里有加法操作，但是不是通过uintptr来执行的吗？这难道就是传说中的golang指针运算？
	fieldBAddress := uintptr(unsafe.Pointer(&x)) + unsafe.Offsetof(x.b)

	//var y [10]int  //这两行是重点
	//a(y)

	// 转换过程：
	// uintptr -> unsafe.Pointer -> *int16
	pb := (*int16)(unsafe.Pointer(fieldBAddress))
	// 修改值
	*pb = 42
	fmt.Println(x.b)
}

// 调用a方法会导致goroutine栈的扩张，即栈区重新复制。
//go:noinline
func a(x [10]int) {
	println(`func a`)
	var y [100]int
	b(y)
}

//go:noinline
func b(x [100]int) {
	println(`func b`)
	var y [1000]int
	c(y)
}

//go:noinline
func c(x [1000]int) {
	println(`func c`)
}
```
在上面方法中，如果不调用`var y [10]int；a(y)`两行代码，`unsafePointer`方法是能正常工作的，即能通过unsafe.Offsetof方法获取field地址并进行赋值。输出修改后的值42.

但是在调用a(y)方法后，这个修改就无效了，输出是0。

总结来看，unsafe的不安全体现在哪里呢？首先应该是**违反了golang的类型系统，一个类型的变量在取地址后，可以转化为unsafe.Pointer类型，这个unsafe.Pointer类型又可以转化为任意类型的指针，这个是不安全的。**，其次**将uintptr转化为unsafe.Pointer类型时，也可能不是安全的，一方面这个uintptr可能不是一个有效的地址，另一个方面就是这个地址可能已经无效了，变量被移动过了。**