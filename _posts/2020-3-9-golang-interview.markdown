---
layout:     post
title:      "golang常见面试题整理"
date:       2020-03-09 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

##### struct以及interface的比较
golang官方对于此问题的描述在[Comparison operators](https://golang.org/ref/spec#Comparison_operators)，这部分操作符有两种：
* 判断是否相等（comparable），==， ！=
* 排序（ordered），<，>，<=，>=

先看comparable，一般基础类型都是可以判断是否相等的，比如Boolean, Integer, Floating-point, Complex, String, Pointer
。

对于Pointer来说，判等运算就是比较的指针的指，如果两个指针指向相同的内存地址，则Pointer相等，当两个指针都是nil的时候也是相等的，这里要求是同类型的，下面的pa以及pb是不能判等的，因为是不同类型：
```go
	var pa *int
	var pb *string
	fmt.Println(pa == pb) // syntax error
```

Channel也是可判等的，条件也是同类型的，chan int与chan string是不能判等的，这个ide会直接提示语法错误，如果两个channel是用同一个make函数生成的，则是相等的，两个同类型的nil channel也是相等的。

Interface也可是可以判等的，两个interface是相等的，如果他们有`identical`的动态类型，以及相等的动态类型值，或者他们都是nil，这里先解释一下[identical](https://golang.org/ref/spec#Type_identity):
文档中说，两个类型，要么是`identical`要么是`different`，两个**命名类型**是否相同的标准如下：
1. 两个命名类型相同的条件是两个类型声明的语句完全相同，关于这点，官网上有句话`A defined type is always different from any other type`，那是否可以理解成命名类型跟其他类型都是非identical的
2. 命名类型和未命名类型永远不相同
3. 两个未命名类型相同的条件是他们的类型声明字面量的结构相同，并且内部元素的类型相同，前一句可以参考官网：对于struct类型来说，如果他们的field顺序是相同的，并且每个field的名字以及类型都是相同的，并且有相同的tag
4. 通过类型别名语句声明的两个类型相同，类型别名的语法为：`type T1 = T2`，T1和T2完全一样。

一个非interface类型跟interface也是可比较的，如果这个非interface类型实现了这个interface，并且interface的动态类型跟前者相同，并且值也相同。

如果struct类型的field是可比较的，那么struct也是可比较的，并且如果field相等，那么struct也是相等的，struct类型比较也是要求类型是`identical`的，下面的语句也会有语法错误：
```go
type ss1 struct {
	a int
	b int
}

type ss2 struct {
	a int
	b int
}

func main()  {
	v1 := ss2{}
	v2 := ss1{}
	fmt.Println(v1 == v2) // error, mismatched type ss1 and ss2
}
```

数组是可以比较的，如果数组的元素是可比较的，每个元素equal，那么数组也equal。

另外需要注意的是：如果interface的动态类型相同，但是动态类型的值不支持比较，会引起运行时panic，这个规则适用于interface或者是含有interface的struct。这个可以用下面例子验证：
```go
type ss1 []int

func (s ss1)Swap(i, j int) {}

func main()  {
	var i1 interface{}
	var i2 interface{}

	i1 = ss1{}
	i2 = ss1{}
	fmt.Println(i1 == i2)
}
```
程序是不报语法错误的，但是运行时会panic，信息为：`panic: runtime error: comparing uncomparable type main.ss1`

最后，`Slice` `Map`以及`function`是不可比较的，但是他们的值可以用nil比较。下面程序会直接报错：`operator == not defined on []int`
```go
	var i1 []int
	var i2 []int

	fmt.Println(i1 == i2) // error
```

##### 关于select
select语句是一种仅能用于通道发送和接收操作的语句。一条selector语句执行时，会选择其中的某个分支并执行。如果select语句中的所有普通case都不满足条件，并且有default，那么default语句会被选中，否则select会阻塞。一个select语句只能包含一个default case，不过它可以放置在该语句的任何位置上。

在开始执行select语句的时候，所有跟在case关键字右边的发送语句或接收语句中的通道表达式或者元素表达式都会被求值，（求值顺序是从左到右，自上而下），无论它所在的case是否有可能被选择都是这样。当有多个case符合条件时，运行时系统会通过一个伪随机的算法选择一个case，其他case会被忽略

##### 短变量声明
使用冒号加等号实现短变量声明，即`a := 3`，由golang运行时系统自动推断类型，短变量声明一般只出现在函数内部，也在`if`,`for`或者`switch`语句中，用于声明临时变量，（全局变量是没办法这么声明的），另外，使用var与短变量声明都是可以自己推断类型的。

##### make与new的区别
内置函数`new`接收一个类型参数，在运行时为其分配一个存储空间，并返回一个指向存储空间的指针，new分配的空间也会被初始化为0值。

内置函数`make`只用于初始化`slice`,`map`,以及`channel`，并可以接收长度等可选参数。make返回的值，并不是指针，分配的空间也是初始化过的。关于为什么使用`make`，这里有自己的一点思考，对于slice来说，其结构可以看做（定义在`src/reflect/value.go`）：
```go
// SliceHeader is the runtime representation of a slice.
// It cannot be used safely or portably and its representation may
// change in a later release.
// Moreover, the Data field is not sufficient to guarantee the data
// it references will not be garbage collected, so programs must keep
// a separate, correctly typed pointer to the underlying data.
type SliceHeader struct {
	Data uintptr
	Len  int
	Cap  int
}
```
也就是说，仅仅需要用new初始化是不够的，需要额外做一些初始化的工作，比如底层数组的布局等。

与此相关联的一个问题是：

`通过new分配空间，与通过变量声明的方式有什么区别？`
```go
i := new(int)

// get a pointer from a variable
var v int
i := &v
```
答案是几乎没有区别的，都是最终调用了`runtime.newobject`来分配空间的。 