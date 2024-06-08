---
layout:     post
title:      "Golang 中的 reflect 机制解析"
date:       2023-10-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Golang
---

**目录**
- [一个具体问题](#一个具体问题)
- [反射相关类型](#反射相关类型)
  - [interface: eface 与 iface](#interface-eface-与-iface)
  - [reflect.Type 接口](#reflecttype-接口)
  - [reflect.Value 结构体](#reflectvalue-结构体)
- [解决具体问题](#解决具体问题)

## 一个具体问题

最近遇到一个问题，在使用工具 [gocsv](https://github.com/gocarina/gocsv) 将一个 csv 文件反序列化为 struct 的时候，反序列出来的字符串都带有一个后缀 `\t`，显然后缀是多余的，但是反序列出来的 struct 字段有上百个， 不可能一个一个检查并去掉 `\t`，于是想用反射来解决这个问题：如果遇到的字段类型是 string 或者 *string 时，检查是否有后缀 `\t`，如果有则去掉。

思路很明确，实现起来却有点摸不着头脑，原来 reflect 我一直没搞清楚。

## 反射相关类型
先通过文档了解下 go 中的基本类型。go 反射机制中最主要的两个方法 reflect.ValueOf 以及 reflect.TypeOf 其参数都是一个 interface{} 类型，在获取其类型、值信息时，都是分解 interface{} 来获取信息的，我们先复习下 go 中的 interface 类型。

### interface: eface 与 iface
eface 是 go 中类型 `interface{}` 也就是 `any` 的底层数据类型，其不包含任何接口方法，任何类型都可以赋值给 interface{}，因此 eface 是不描述实际类型的方法信息的，只包含两部分信息：1）实际类型的类型信息；2）指向实际类型的指针。
```go
type eface struct {
	_type *_type         // 具体实例的类型信息。
	data  unsafe.Pointer // 指向接口具体实例的指针。
}
```
其中 _type(位于 src/runtime/types.go) 是 go 中所有类型的基本类型，所有类型都是由这些数据类型加上其他信息组成的，这个类型与 reflect.Type 接口实现者 rtype(位于 src/reflect/type.go) 是一致的，都是 `abi.Type` 类型。
```go
type _type = abi.Type

// rtype is a wrapper that allows us to define additional methods.
type rtype struct {
	*abi.Type // embedding is okay here (unlike reflect) because none of this is public
}

type slicetype struct { // 其他类型都是由 _type 字段加上其他信息组成的。
  typ _type  // slice 自身类型
  ele *_type // 元素类型
}
```
其中 abi.Type 的定义为(位于 src/internal/abi/type.go)。这个类型现在不展开介绍了，其描述了一些类型的元信息。
```go
type Type struct {
	Size_       uintptr
	PtrBytes    uintptr // number of (prefix) bytes in the type that can contain pointers
	Hash        uint32  // hash of type; avoids computation in hash tables
	TFlag       TFlag   // extra type information flags
	Align_      uint8   // alignment of variable with this type
	FieldAlign_ uint8   // alignment of struct field with this type
	Kind_       uint8   // enumeration for C
	Equal func(unsafe.Pointer, unsafe.Pointer) bool
	// GCData stores the GC type data for the garbage collector.
	// If the KindGCProg bit is set in kind, GCData is a GC program.
	// Otherwise it is a ptrmask bitmap. See mbitmap.go for details.
	GCData    *byte
	Str       NameOff // string form
	PtrToThis TypeOff // type for pointer to this type, may be zero
}
```
iface 与 eface 基本类似，与 eface 不同，iface 带有一个接口集合，比如 io 包中的 Reader 与 Writer 接口。因此在 iface 的结构体中，需要有字段来描述这些方法信息，这个字段是 `itab`，其中 interfacetype 中的 Methods 描述的接口的方法列表。
```go
type iface struct {
	tab  *itab
	data unsafe.Pointer
}
type itab struct {
	inter *interfacetype
	_type *_type
	hash  uint32 // copy of _type.hash. Used for type switches.
	_     [4]byte
	fun   [1]uintptr // variable sized. fun[0]==0 means _type does not implement inter.
}
type InterfaceType struct {
	Type
	PkgPath Name      // import path
	Methods []Imethod // sorted by hash
}
```
### reflect.Type 接口
在大概了解了 go 中的 interface 之后，我们来看下反射中的相关类型，首先我们需要注意，reflect.Type 是一个接口，我们通过 `reflect.TypeOf(a any) Type` 来得到一个这样的接口，这个接口只描述类型信息，没有值信息。
reflect.TypeOf 的实现也相对比较明确，从下面代码看，就是取了 eface 的 _type 字段，也就是最终是 abi.Type 类型，其实现了 reflect.Type 接口。
```go
func TypeOf(i any) Type {
	eface := *(*emptyInterface)(unsafe.Pointer(&i))
	return toType((*abi.Type)(noescape(unsafe.Pointer(eface.typ))))
}
```
那接口都包含哪些方法呢？首先我们知道这个接口只描述类型信息，不包含值信息，完整的方法列表可以参考 [https://pkg.go.dev/reflect#Type](https://pkg.go.dev/reflect#Type)，这里挑选几个常用的分析一下。还有个问题需要注意一下，虽然 Type 接口定义了很多接口，**但是这些接口是分类型的，也就是一部分接口只适用于特定的类型，其他类型调用那些接口时则会 panic**，所以在使用接口时需要注意一下。

* Kind() Kind: 返回 go 预定义基本类型的字面表示（使用 iota 生成的数字），如 Bool、String、Pointer(Ptr)、Interface、Struct、Func等。基本上，我们可以通过这个方法来判断变量或者字段的类型。
* Elem() Type: 返回一个类型的底层类型，这个方法只适用于 Array/Chan/Map/Pointer/Slice。对于 Array 等表示的数组元素的类型，对于指针类型来说，该方法返回指向的类型。比如 *string 的 Elem() 方法返回的 Type，其 Kind 为 String。需要注意 reflect.Value 结构体（是结构体，而非 interface）也有 `Elem() Value` 方法，不过其返回值是 Value 结构体，不要混淆。
* Field(i int) StructField: 这个方法只适用于 Struct 类型，否则会 panic。这个方法返回的类型为 `StructField`，当我们读到这里的时候感觉没什么，但是当我们知道 reflect.Value 接口也有 `Field(i int) Value` 方法的时候，就会感觉有点迷惑（go reflect 令人难以理解的一个原因是其类型和接口很多相似的，很容易产生混淆）。需要注意的是，reflect.Value 接口其 `Field(i int) Value` 方法返回的类型也是 Value，跟 Elem() 也返回 Type 一样，看上去类似于递归调用。根据 StructField 类型，当我们需要解析 struct tag 的时候，需要用到这个方法。
  ```go
  type StructField struct {
    Name string // field 名字
    PkgPath string
    Type      Type      // field type
    Tag       StructTag // field tag 标签
    Offset    uintptr   
    Index     []int     
    Anonymous bool    
  }
  ```
* Len() int: 返回数组类型实例的长度，非数组则 panic。
* In(i int) Type: 返回 function 类型的第 i 个参数的类型，如果类型的 kind 不是 Func 则 panic。同样，如果 i 不在区间 `[0, NumIn())` 之间时，也 panic。

### reflect.Value 结构体
我们通过 `reflect.ValueOf(a any) Value` 方法可以得到任意类型的 Value 信息。其定义如下（为 src/reflect/value.go）。从 [Value 的定义](https://cs.opensource.google/go/go/+/refs/tags/go1.22.4:src/reflect/value.go;l=39) 中，可以看到非常类似 eface 的定义，包含类型信息，以及指向数据的指针。其中 flag 字段表示 Value 的元数据，比如是否可以 Set。
```go
type Value struct {
	typ_ *abi.Type
	ptr unsafe.Pointer
	flag
}
```
基本上 Value 能拿到类似 reflect.Type 接口的所有信息，至少 Value 结构体调用 Type() Type 方法就能拿到类型信息，因此如果我们如果只关注类型本身的信息可以通过 reflect.TypeOf 方法拿到类型信息，其他情况下用 reflect.ValueOf 方法就够了。

Value 同样有很多方法，参考 [https://pkg.go.dev/reflect#Value](https://pkg.go.dev/reflect#Value)，同样，我们只选择几个介绍一下。
* CanSet() bool: 表示 Value 是否可以被修改（重写），value 可被修改必须是 addressable，并且不是结构体的 unexported 字段。一般来说，只有一个 Value 被指针指向时才会被改写。并且是指针的 Elem() Value 可被改写，指针不是可改写的。比如下面例子
  ```go
  	a := tea.String("abc")
	  va := reflect.ValueOf(a)
	  fmt.Println(va.CanSet())        // false
	  fmt.Println(va.Elem().CanSet()) //true
  ```
* Elem() Value: 返回指针指向的值的 Value 或者 interface 真实值的 Value。如果 Pointer 或者 interface 为 nil，则该方法返回 nil。（注意区别 Type 的 Elem 方法）
* IsNil() bool: 判断 Value 是不是 nil，Value 必须是 chan/func/interface/map/pointer/slice。
* Field(i int) Value: 返回结构体的第 i 个字段，该方法返回的仍然是一个 Value 类型，类似于递归。（注意区别 Type 的 Value 方法，后者因为关注类型本身所以返回的是 StructField 结构体，不包含值信息）。
* Interface() (i any): 将当前 Value 作为 interface{} 返回，类似于：`var i interface{} = (Value's underlying value)`
* Kind() Kind: 返回类型，这个跟 Type 接口的 Kind 方法是一致的。
* Type() Type: 返回 Value 的 Type 类型，跟对变量调用 reflect.TypeOf 方法效果是一样的。

## 解决具体问题
解决具体的方法很简单，如下，注意改方法的参数必须是指针类型，不然我们是无法修改字段的值的也就是无法 CanSet()。
```go
func TrimStringFields(instance interface{}) {
	v := reflect.ValueOf(instance).Elem() // 返回指针指向的类型的 Value，不是 pointer 就 panic

	for i := 0; i < v.NumField(); i++ {
		field := v.Field(i)       // 返回字段的 Value
		fieldType := field.Type() // 返回字段的 Type

		if fieldType.Kind() == reflect.String {
			sv := strings.TrimSpace(field.String())
			field.SetString(sv)
		} else if fieldType.Kind() == reflect.Ptr && fieldType.Elem().Kind() == reflect.String {
			if field.IsNil() { // panic: reflect: call of reflect.Value.SetString on zero Value
				return
			}
			sv := strings.TrimSpace(field.Elem().String())
			field.Elem().SetString(sv)
		}
	}
}
```
另外需要注意，我们再给 interface{} 赋值时，也是值传递，对于 `var i interface{} = Value`。如果 Value 是 int/struct 等，则 interface{} 保存的是副本，如果是指针，则是一个指针的副本。