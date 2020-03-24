---
layout:     post
title:      "golang string小结"
date:       2020-03-23 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

#### 基础
在Go语言中，字符串也是一种基本类型。字符串中的内容可以用类似数组下标的方式获取，但与数组不同，字符串的内容不能在初始化后被修改，比如下面的例子：
```go
    str := "Hello world"
    str[0] = 'X'    //编译错误：cannot assign to str[0]
    a := &str[0]    //编译错误：cannot take address of str[0]
```
同样的道理，我们是不能对下标元素取地址的。根据golang spec，在等号左边的变量必须是可取地址的，这样才能给它赋值。虽然但是不能对单个元素赋值，但是很显然，我们是可以对真个字符串重新赋值的，下面代码是没问题的：
```go
	str := "Hello world"
	str = "another"
	fmt.Println(str)
```

我们可以使用内置函数len()来获取字符串的长度，这个内置函数返回的是字节（byte）数，比如下面，这个问题后面还会讲：
```go
	str := "你好世界"
	fmt.Println(len(str)) // 输出12，每个中文字符占3个字节
```

遍历，我们有两种方式遍历字符串，一种是使用下标（遍历得到的元素类型是byte，`type byte = uint8`，即是uint8类型），一种是使用for-each循环（得到的是rune类型，定义是：`type rune = int32`，即是int32类型，为啥还要带符合呢？）
```go
    str := "你好世界"
    // 使用下标遍历
	for i:=0; i<len(str); i++ {
		fmt.Println(i, reflect.TypeOf(str[i]), str[i])
    }

// 输出为：
0 uint8 228
1 uint8 189
2 uint8 160
3 uint8 229
4 uint8 165
5 uint8 189
6 uint8 228
7 uint8 184
8 uint8 150
9 uint8 231
10 uint8 149
11 uint8 140
```
```go
	str := "你好世界"
	for i, c := range str {
		fmt.Println(i, reflect.TypeOf(c), c)
    }
// 输出如下，可以观察到下标是不连续的
0 int32 20320
3 int32 22909
6 int32 19990
9 int32 30028
```

#### 运行时的表示
`reflect.StringHeader`类型显示了string在运行时的表示，其定义如下：
```go
// 使用uintptr表示指针，不能保证其指向的内容不会被垃圾回收器回收
type StringHeader struct {
	Data uintptr
	Len  int
}

// 用unsafe.Pointer表示指针
type stringHeader struct {
	Data unsafe.Pointer
	Len  int
}
```
从定义上看，string可以认为是一个只读的byte数组。golang数组跟C语言数组类似，数组的首地址也是第一个元素的地址，这个可以通过下面代码验证：
```go
	a := [3]int{1, 2, 3}
	fmt.Printf("%p\n", &a)
	fmt.Printf("%p\n", &(a[0]))
	// 输出一致
	//0xc420014360
	//0xc420014360
```
在golang中，字符串赋值以及使用slice取子字符串时，结果与原数组是共享底层数组的，同样，结果也是不可写的。

#### []byte与string转化的开销
* 字符串类型可以强制转换为[\]byte类型，[]byte类型也可以被转换为string类型。
* 字符串类型可以强制转换为[\]rune类型，[]rune类型也可以强制转换为string类型。

当字符串与[\]byte类型相互转换的时候，其实底层是发生了deepcopy，这个很好理解，string转换为[\]byte的时候，[]byte是可以写的，但是原来的string是不可写的，那就需要发生deepcopy了。

string与[]byte转换时的编译优化：
string与[]byte相互转换时不一定总是发生deepcopy，比如下面几种情况，总的来说，就是发生了强制转换，但是对转换后的结果进行了读操作，不需要写操作，这个时候可以不用发生深拷贝。

* string转换为[\]byte时，[]byte作为for range循环用，比如：
```go
	for i, b := range []byte(str) {
		fmt.Println(i, ":", b)
	}
```

* []byte转换为string的时候，string作为map的key，比如：
```go
	key := []byte{'k', 'e', 'y'}
	m := map[string]string{}
	// Here, the string(key) conversion will not copy
	// the bytes in key. The optimization will be still
	// made, even if key is a package-level variable.
	m[string(key)] = "value"
```

* []byte转换为string的时候，用来做比较运算。
* a conversion (from byte slice to string) which is used in a string concatenation, and at least one of concatenated string values is a non-blank string constant.

#### 字符串拼接
参考链接给了一篇分析性能的文章（跑了一些UT），这里直接给出结论：
* +拼接适合用于数量较小的、常量字符串，这个时候编译器会给我们优化，优化的结果就是在编译时直接把+号两边的字符串拼成一个字符串。这个只能是常量，变量在编译期间值还是未确定的。
* builder从性能上还是灵活性上都是不错的。其使用方式如下
```go
var b strings.Builder
b.WriteString("abc")
b.WriteString("efd")
str := b.String()
```
大概翻了一下strings.Builder的实现，就是持有一个[\]byte的缓存，每次写操作就是对这个[\]byte执行append操作。如果空间不够了就执行grow，这个grow好像跟[]byte内部的grow区别也不大，就是增大为2倍(还有额外的n个空间)，主要代码如下：
```go
// A Builder is used to efficiently build a string using Write methods.
// It minimizes memory copying. The zero value is ready to use.
// Do not copy a non-zero Builder.
type Builder struct {
	addr *Builder // of receiver, to detect copies by value
	buf  []byte
}

// String方法直接使用unsafe.Pointer对不同类型的指针进行了转换
// 这样避免了deepcopy操作
func (b *Builder) String() string {
	return *(*string)(unsafe.Pointer(&b.buf))
}

// grow copies the buffer to a new, larger buffer so that there are at least n
// bytes of capacity beyond len(b.buf).
// grow操作就是make一个新的slice，然后把数据拷贝过去
func (b *Builder) grow(n int) {
	buf := make([]byte, len(b.buf), 2*cap(b.buf)+n)
	copy(buf, b.buf)
	b.buf = buf
}

// WriteString appends the contents of s to b's buffer.
// It returns the length of s and a nil error.
// WriteString就是直接对[]byte进行append操作，注意"s..."这种语法糖，把s看成了[]byte
func (b *Builder) WriteString(s string) (int, error) {
	b.copyCheck()
	b.buf = append(b.buf, s...)
	return len(s), nil
}
```

#### 参考

[Strings in Go](https://go101.org/article/string.html)

[Go语言字符串高效拼接（二）](https://www.flysnow.org/2018/11/05/golang-concat-strings-performance-analysis.html)