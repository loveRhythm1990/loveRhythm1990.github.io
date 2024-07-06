---
layout:     post
title:      "Go 中的 errors.Is 与 errors.As"
date:       2023-11-18 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Golang
---

**目录**
- [概述](#概述)
- [通过 fmt.Errorf 来 wrap error](#通过-fmterrorf-来-wrap-error)
- [errors.Is: 是否有错误 equal](#errorsis-是否有错误-equal)
  - [理解与使用](#理解与使用)
  - [errors.Is 实现](#errorsis-实现)
- [errors.As: 是否有错误类型匹配](#errorsas-是否有错误类型匹配)
  - [理解与使用](#理解与使用-1)
  - [errors.As 实现](#errorsas-实现)
- [Go 中的 Comparable](#go-中的-comparable)
  - [struct](#struct)
  - [指针](#指针)
  - [interface](#interface)

### 概述
[errors](https://pkg.go.dev/errors#pkg-functions) 包中的 Is 方法与 As 方法理解起来有点抽象，如果使用不当可能会引发代码逻辑错误，本文目标是明确 Is 与 As 的使用场景，并了解下内部实现。

### 通过 fmt.Errorf 来 wrap error
fmt.Errorf 最简单的使用在于产生一个错误，如：`err := fmt.Errorf("this is an error")`，但还有一种使用方式是 wrap 一个 err 并产生一个新的 error，语法如下，注意要使用 %w 占位符。
```go
wrapsErr := fmt.Errorf("... %w ...", ..., err, ...)
```
err 经过多次 wrap 之后，所有的 err 会形成一棵树，树的一个节点有可能有多个 err，因为可能通过多个 %w 占位符 wrap 多个 error。总结起来 wrap error 原因有：1）形成一个错误链，比如 A 方法调用 B 方法，B 方法调用 C 方法，当 C 方法返回错误时，B 方法和 C 方法都可以 wrap error 再返回，wrap 的时候，会带上自身的一些 context 信息。2）辅助定位问题，打印外层错误时，会将整个链上附带的信息都打印出来。如下面的输出内容为：outer2: outer1: not found
```go
func main() {
	notFound := errors.New("not found")
	err1 := fmt.Errorf("outer1: %w", notFound)
	err2 := fmt.Errorf("outer2: %w", err1)
	fmt.Println(err2.Error())
}
```

### errors.Is: 是否有错误 equal

#### 理解与使用
首先要注意的是，errors.Is 判断的是是否有错误`相等`，是通过比较运算符 `==` 进行判断的，因此就算是同一个错误类型，但是错误信息不同，也是不相等的。Is 方法与使用 `==` 的区别是前者会遍历整个错误链（本文后面的 As 也是如此）。Is 方法要求第二个参数是 error 类型即可。

Is 在判断是否相等时，会遍历整个错误链（深度优先遍历），检查链上每个错误是否与给定的错误相等，如果发现有一个相等就返回 true。另外，如果错误实现了 Is(error) bool 接口，还会调用 Is 方法来判断是否相等，在下面的小例子中，因为 Is 方法总是返回 true。对任何错误类型，对 f 调用 errors.Is 的结果都将为 true，即其与任何错误都是`相等`的。
```go
type foo struct{}
func (f *foo) Error() string { return "foo error" }
func (f *foo) Is(err error) bool { return true }

func main() {
	f := &foo{}
	randomErr := errors.New("random error")
	fmt.Println("always true:", errors.Is(f, randomErr))
}
```

#### errors.Is 实现
这一小章节，我们看一下 Is 的具体实现，判断按照下面顺序执行。

1）如果 target 可比较，则利于 `==` 运算符判断两个 error 是否相等。这里有个细节是没有判断第一个参数 err 是否是可比较的，因为若其类型跟 target 不一致，则 interface 在比较时直接返回 false；若类型一致，则是可比较的。本文在末尾章节补充了 Go comparable 的一些内容。

2）如果 err 实现了 Is(error) bool 方法，则调用其 Is 方法进行判断，如果方法返回 true，则直接返回 true。否则要继续遍历其他节点比较。在判断 err 是否实现 Is 方法时，是通过类型断言 `err.(interface{ Is(error) bool })` 来实现的，注意类型断言只适用于 interface 类型。

3）遍历其他节点，首先使用 switch-type 语句来判断 err 实现的 `interface{ Unwrap() error }` 方法还是 `interface{ Unwrap() []error }` 方法。这里调用 Unwrap 跟在 Tree 中调用 LeftChild/RightChild 是一样的。Unwrap 返回的值，覆盖旧的 err 值，交给 for 循环继续检查遍历。另外需要注意，如果 Unwrap 返回 nil 说明是叶子节点。

```go
func Is(err, target error) bool {
	if target == nil {
		return err == target
	}

	isComparable := reflectlite.TypeOf(target).Comparable()
	for {
		if isComparable && err == target {
			return true
		}
		if x, ok := err.(interface{ Is(error) bool }); ok && x.Is(target) {
			return true
		}
		switch x := err.(type) {
		case interface{ Unwrap() error }:
			err = x.Unwrap()
			if err == nil {
				return false
			}
		case interface{ Unwrap() []error }:
			for _, err := range x.Unwrap() {
				if Is(err, target) {
					return true
				}
			}
			return false
		default:
			return false
		}
	}
}
```

### errors.As: 是否有错误类型匹配

#### 理解与使用
As 方法判断错误链中，是否存在类型匹配的错误，如果存在则将该错误赋值给 target 参数，并返回 true。target 参数在下面几种情况下会发生 panic：1）等于 nil；2）不是一个指针，或者是一个指向 nil 的指针，比如 `b := (*error)(nil)`，b 不能作为 target；3）*target 指向的类型，必须是 interface 或者实现了 error。

#### errors.As 实现
As 方法的实现与 Is 基本类似，都是遍历错误链，并检查是否有错误类型匹配。在 As 的实现中，涉及到很多 Go reflect 中的内容。在 Go 中 [Type](https://pkg.go.dev/reflect#Type) 类型是描述类型的接口，这个接口的下面几个接口需要关注一下：1）Implements，描述类型是否实现了 u 接口，其参数 u 必须是 interface 类型，否则会 panic。2）AssignableTo，是否可以赋值给 u，分为两种情况：直接赋值和实现接口，参数 u 不能为 nil。3）Comparable，参考文章末尾的 Comparable 章节。4）Elem，返回指针指向的变量的类型。

```go
type Type interface {
	Implements(u Type) bool
	AssignableTo(u Type) bool
	Comparable() bool
	Elem() Type

    // ...
}
```
As 方法在发现类型匹配的 error 后（对类型调用 AssignableTo 返回 true），会调用 Value 方法的 Elem().Set 原地修改 target 的值。在 Go 反射机制中，Value 负责获取和修改值信息（CanSet 方法返回 true 时才可修改），其是一个结构体；Type 接口负责类型信息，其是一个接口。两者有很多相似的方法，需要注意区分。
```go
func As(err error, target any) bool {
	if err == nil { return false }
	if target == nil { panic("errors: target cannot be nil") }
	val := reflectlite.ValueOf(target)
	typ := val.Type()
	if typ.Kind() != reflectlite.Ptr || val.IsNil() {
		panic("errors: target must be a non-nil pointer")
	}
	targetType := typ.Elem()
	if targetType.Kind() != reflectlite.Interface && !targetType.Implements(errorType) {
		panic("errors: *target must be interface or implement error")
	}
	for {
		if reflectlite.TypeOf(err).AssignableTo(targetType) {
			val.Elem().Set(reflectlite.ValueOf(err))
			return true
		}
		if x, ok := err.(interface{ As(any) bool }); ok && x.As(target) {
			return true
		}
		switch x := err.(type) {
		case interface{ Unwrap() error }:
			err = x.Unwrap()
			if err == nil { return false }
		case interface{ Unwrap() []error }:
			for _, err := range x.Unwrap() {
				if As(err, target) { return true }
			}
			return false
		default:
			return false
		}
	}
}
```

### Go 中的 Comparable
本章节复习下 Go 中 comparable 的定义。
Golang [Comparison_operators 文档](https://go.dev/ref/spec#Comparison_operators) 描述了类型是否 comparable（可使用运算符 ==，!=），以及是否可排序（可使用运算符：<，<=，>，>=）。具体来说有下面三类：

|  类型   | 可比较(comparable)  | 比较规则|可排序(ordered)|
|  ----  | ----  |----|----|
| Boolean  | 是	 |都为 true 或者都为 false|否|
| Integer  | 是	 |由于众所周知的原因|是|
|Float|是|-|是|
|Complex|是|equal if: both real(u) == real(v) and imag(u) == imag(v)|否|
|String|是|-|是|
|Pointer|是|指向同一个变量，或者都为 nil|否|
|Channel|是|由同一个 make 初始化，或者都为nil，都为 nil 相等的前提是类型相同，如 chan int 和 chan float64 是不等的| 否|
|Interface|是|都为 nil；或者底层变量的类型一致，并且值相等，底层变量不可比较时，会发生运行时 panic|否|
|非 interface 类型与 interface 类型|是|规则同 Interface|否|
|Struct|不确定|只有当每个字段都可比较时才可比较，比较时依次比较每个字段|否|
|Array|不确定|只有当数组元素可比较时才可比较|否|
|Slice|否|-|否|
|Map|否|-|否|
|Function|否|-|否|

#### struct
对 struct 来讲，只有当字段可比较时，才能用 `==` 比较两个变量。否则会发生编译错误。
```go
type foo struct{ s []string }
type bar struct{ a int }
func main() {
	b1 := bar{a: 1}
	b2 := bar{a: 1}

	fmt.Println(b1 == b2) // comparable, true

	f1 := foo{}
	f2 := foo{}
	fmt.Println(f1 == f2) // 编译错误 Invalid operation: f1 == f2 (the operator == is not defined on foo)
}
```

#### 指针
对指针来讲，只会看是否指向同一个对象，以及是否都为 nil。需要注意的是，指针在比较时，并不比较指向的变量是否相等，可以看下面例子。这点在 interface 的底层类型是指针时需要注意一下。
```go
func main() {
	b1 := 1
	b2 := 1
	pb1 := &b1
	pb2 := &b2
	fmt.Println(pb1 == pb2) // false
}
```
#### interface
比较两个 interface 时，如果底层类型不可比较，会发生运行时 panic。如下 foo 因为含有 []string 是不可比较的。
```go
type foo struct{ s []string }

func main() {
	var ea interface{} = foo{}
	var ea2 interface{} = foo{}
	fmt.Println(ea == ea2) // panic: runtime error: comparing uncomparable type main.foo
}
```
但是 interface 比较还有一个特例，如果两个变量都是 interface，并且有一个变量的底层类型是可比较的，那么不管另一个变量是不是可比较，都不会发生 panic，因为在判断是否相等时会先判断类型是否一致。
```go
type bar struct{ a int }
type foo struct{ s []string }
func (f foo) Error() string { return "foo error" }

func main() {
	var ea interface{} = foo{}
	var ea2 interface{} = bar{}
	fmt.Println(ea == ea2) // false
}
```

参考：

[All your comparable types](https://go.dev/blog/comparable)