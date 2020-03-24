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

#### 参考

[Strings in Go](https://go101.org/article/string.html)

