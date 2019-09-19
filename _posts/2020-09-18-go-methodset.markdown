---
layout:     post
title:      "结构体与结构体指针的方法集"
subtitle:   " \"简介\""
date:       2019-09-18 22:32:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

> golang基础知识

最开始遇到这问题是在看golang堆的实现的时候，堆的接口是下面这样的，一共需要实现五个接口。
```go
type Interface interface {
    // 排序需要实现的接口
    sort.Interface
    Push(x interface{}) // add x as element Len()
	Pop() interface{}   // remove and return element Len() - 1.
}
```
golang文档提供了一个示例如下，定义了一个IntHeap类型，奇怪的是实现接口的五个方法时，有三个是值类型（value receiver），另外两个是指针类型（pointer receiver），那IntHeap到底有没有实现这个`Interface`接口呢？
```go
// An IntHeap is a min-heap of ints.
type IntHeap []int

func (h IntHeap) Len() int           { return len(h) }
func (h IntHeap) Less(i, j int) bool { return h[i] < h[j] }
func (h IntHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }

func (h *IntHeap) Push(x interface{}) {
	// Push and Pop use pointer receivers because they modify the slice's length,
	// not just its contents.
	*h = append(*h, x.(int))
}

func (h *IntHeap) Pop() interface{} {
	x := (*h)[len(*h)-1]
	*h = (*h)[0 : len(*h)-1]
	return x
}
```
我们先来看方法集的概念，go语言规范[Method_sets](https://golang.org/ref/spec#Method_sets)的说明如下：
> A type may have a method set associated with it. The method set of an interface type is its interface. The method set of any other type T consists of all methods declared with receiver type T. The method set of the corresponding pointer type *T is the set of all methods declared with receiver *T or T (that is, it also contains the method set of T). Further rules apply to structs containing embedded fields, as described in the section on struct types. Any other type has an empty method set. In a method set, each method must have a unique non-blank method name.
The method set of a type determines the interfaces that the type implements and the methods that can be called using a receiver of that type.

另外，go语言规范关于方法调用 [calls](https://golang.org/ref/spec#Calls)时有如下说明:
> A method call x.m() is valid if the method set of (the type of) x contains m and the argument list can be assigned to the parameter list of m. If x is **addressable** and &x's method set contains m, x.m() is shorthand for (&x).m()

总结起来，对于值类型T，及对应的指针类型*T：
* T的方法集包括接收者为类型T（Value-receiver）的方法
* \*T的方法集方法包括接收者为类型T的方法，以及方法为类型*T的方法，即为两者的并集
* Interface类型的方法集为Interface类型中声明的方法。
* 在进行方法调用时，如果变量是可寻址(`addressable`)的，则类型T可以调用*T方法集中的方法。

基于以上总结，下面代码`aHeap.Pop()`是合法的，因为aHeap是可以寻址的。另外，需要主要的是方法`heap.Init(h Interface)`的参数是一个接口，存在接口中的真实值是不可寻址的，所以必须传一个指针（可以说`*IntHeap`是实现了heap Interface的），另外map中的元素也是不可寻址的。
```golang
func main() {

	aHeap := IntHeap([]int{1,2})
	// Init的参数必须为指针类型
	heap.Init(&aHeap)
	// 这是合法的： Pop在指针类型的方法集中，可以被非指针调用，因为是可寻址的
	aHeap.Pop()
}
```
关于是不是可以寻址可以参数下面参考[go addressable 详解](https://colobu.com/2018/02/27/go-addressable/)，说的已经很清楚了。另外以后希望会对Interface和Map的元素不能寻址做分析。

### 参考：
* [go语言规范](https://golang.org/ref/spec#Method_sets)
* [MethodSets](https://github.com/golang/go/wiki/MethodSets)
* [go addressable 详解](https://colobu.com/2018/02/27/go-addressable/)