---
layout:     post
title:      "golang内存逃逸分析"
date:       2020-1-10 17:03:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

首先是内存逃逸的定义，我倾向于使用这篇文章中的说法 [Golang Escape Analysis: reduce pressure on GC!](https://medium.com/faun/golang-escape-analysis-reduce-pressure-on-gc-6bde1891d625) :

> So what is Escape Analysis? What is the intelligence it provides to the Golang compiler? It checks a memory that it really needs to be allocated at a heap or it could be managed within a stack itself. The decision would be made by checking whether a particular memory needs to be shared across stack frames (function boundaries). If not, it would place it in a stack, else in heap memory

逃逸分析是编译器的行为，用户是感知不到的，但是可以通过`go build -gcflags="-m -m"`来查看。一般来说，golang内存逃逸有下面几个问题：
* 什么时候从栈空间逃逸到堆空间
* 什么时候只需要在栈上分配空间
* 目前golang逃逸有什么缺陷
* 对垃圾回收（gc）有什么影响？

稍微分析一下上面这些问题，遗憾的是目前并不能给出完整答案，在了解golang的gc后再补充一下文章。

### 栈空间逃逸到堆空间
这个比较符合`逃逸分析`这个名字，毕竟这才是一种逃逸行为，这个我目前感觉在golang语法支持下是不得不发生的行为。对于这方面，网上讨论的较多，本文基于的golang版本为go1.12.7

#### 返回局部变量的地址
这个是最常见的情况，参考下面代码：
```go
package escape

func Fun() *int {
	a := 10
	return &a
}
```
运行，go build -gcflags="-m -m"
```bash
# k8s.io/kubernetes/aaTest/escape
./escape.go:3:6: can inline Fun as: func() *int { a := 10; return &a }
./escape.go:5:9: &a escapes to heap
./escape.go:5:9:        from ~r0 (return) at ./escape.go:5:2
./escape.go:4:2: moved to heap: a
```
此函数不返回指针是不用逃逸的

#### 栈空间不足
分配的栈空间太大，
```go
package escape

func Fun() {
	_ = make([]int, 1000, 1000)
}

func Fun1() {
	_ = make([]int, 10000, 10000)
}
```
运行，go build -gcflags="-m -m"。给出的逃逸的原因是`too large for stack`
```
# k8s.io/kubernetes/aaTest/escape
./escape.go:3:6: can inline Fun as: func() { _ = make([]int, 1000, 1000) }
./escape.go:7:6: can inline Fun1 as: func() { _ = make([]int, 10000, 10000) }
./escape.go:4:10: Fun make([]int, 1000, 1000) does not escape
./escape.go:8:10: make([]int, 10000, 10000) escapes to heap
./escape.go:8:10:       from make([]int, 10000, 10000) (too large for stack) at ./escape.go:8:10
```


#### 动态类型逃逸
如果函数的的参数为interface，编译期间很难确定其参数的具体类型，也能产生逃逸。


### 目前golang逃逸的缺陷
这个主要是目前逃逸分析粒度较粗，在不需要发生逃逸的情况下发生了内存逃逸，这个主要参考：

[Escape-Analysis Flaws](https://www.ardanlabs.com/blog/2018/01/escape-analysis-flaws.html)

[Go Escape Analysis Flaws](https://docs.google.com/document/d/1CxgUBPlx9iJzkz9JWkb6tIpTe5q32QDmz8l0BouG0Cw/edit#)

这篇文章介绍了几种没必要发生逃逸的情况。

### 对GC的影响
分配在栈上的变量在函数返回后就回收了，不需要gc的参与；分配在堆上的空间是需要GC的。

### 参考
[Golang内存分配逃逸分析](https://driverzhang.github.io/post/golang%E5%86%85%E5%AD%98%E5%88%86%E9%85%8D%E9%80%83%E9%80%B8%E5%88%86%E6%9E%90/)

[Golang Escape Analysis](https://topic.alibabacloud.com/a/golang-font-colorredescapefont-analysis_1_38_30920227.html)
