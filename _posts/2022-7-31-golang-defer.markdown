---
layout:     post
title:      "Golang 中的 defer 关键字"
date:       2022-07-31 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Golang
---

**文章目录**
- [happy case](#happy-case)
- [引用外部变量](#引用外部变量)
  - [函数参数](#函数参数)
  - [闭包引用](#闭包引用)
- [defer/recover 捕获 panic](#deferrecover-捕获-panic)
- [修改函数返回值](#修改函数返回值)
  - [非命名返回值](#非命名返回值)
  - [命名返回值](#命名返回值)
- [执行顺序](#执行顺序)

Golang 中的 defer 关键字允许我们在函数退出的时候执行一些资源回收操作，比如关闭数据库连接、关闭文件等。常规使用 defer 时一般没什么问题，但是遇到 corner case 的时候总是有点搞不清，这里记录一个笔记。

### happy case
正常使用 defer 的情况如下，比如函数打开一个文件，在函数返回的时候，关闭这个文件，否则会有文件泄漏 `too many open files` 的问题。
```go
func foo() error {
  f, err := os.Open("/home/test")
  if err != nil {
    return err
  }
  defer func(){
    f.Close()
  }
  // ...
  return nil
}
```

### 引用外部变量
引用外部变量的方式有两种，一种情况是函数参数，另一种情况是闭包引用。前者在 defer 定义的时候就把值传递给 defer，并被 cache 起来；后者则在 defer 函数中真正调用时根据上下文确定当前的值。
#### 函数参数
函数参数也是值传递，如果是值类型，则是一个完全拷贝，如果是引用类型，则类似于一个指针，分别看下面例子。注意在第二个例子中，如果我们使用 `a = append(a, "a")` 重新赋值 a，这样对 defer 中的参数是没有影响的，因为对 append 是对 SliceHeader 的一个拷贝，结构体本身是值类型，但是结构体有一个数值指针。因此需要记住 `append` 对 slice 的修改不是原地的，在需要追加操作时，需要返回一个新的 slice。
```go
func paramValue() {
	a := 1
	defer func(p int) {
		fmt.Println("value p:", p) // 打印：value p: 1
	}(a)
  a = 34
}

func paramRef() {
	a := make([]string, 2)
	defer func(p []string) {
		fmt.Println("ref p:", p) // 打印 ref p: [a b]
	}(a)
	a[0] = "a"
	a[1] = "b"
}
```

#### 闭包引用
闭包引用，在实际调用函数时，会根据上下文来决定引用变量的值。如在下面的例子中，将打印 2。在 Golang 中，闭包是一个匿名函数以及该匿名函数引用的函数外的变量的整体，即闭包等于：匿名函数 + 引用的外部变量。在执行闭包函数的时候，引用的外部变量的值即是在调用时刻该外部变量的值。在下面例子中，闭包引用了外部变量 i，在 defer 声明的时候，i 的值为 0，但在执行的时候，其值是 2.
```go
func main() {
	closure()
}
func closure() {
	i := 0
	defer func() {
		fmt.Println(i) // 打印 2
	}()
	i = 2
}
```

### defer/recover 捕获 panic
在 golang 中，如果一个 goroutine 发生了 panic，那么整个程序将会退出。为了避免这种情况，golang 支持使用 recover 方法捕获 goroutine 发生的 panic，并做一些善后处理。recover 方法只能在 defer 中调用才有效。另外 golang 中 panic 方法的参数和 recover 返回值都是 any 类型（interface{}）。
```go
func deferRecover() {
	defer func() {
		if a := recover(); a != nil {
			fmt.Println("catch a panic:", a)
			// 打印：catch a panic: [some thing unexpected]
		}
	}()
	subFunc()
}
func subFunc() {
	panic([]string{"some thing unexpected"})
}
```
golang 的 recover 只能捕获同一个 goroutine 发生的 panic，对其他 goroutine 发生的 panic 无法捕获。并且在 panic 发生之后也无法捕获，比如下面的例子无法捕获。
```go
func parent() {
	panic("panic")
	deferRecover()
}
func deferRecover() {
	defer func() {
		if a := recover(); a != nil {
			fmt.Println("catch a panic:", a)
		}
	}()
}
```

### 修改函数返回值
这里主要是理解 golang `return` 关键字不是原子的，具体是有三条指令：1）返回值 = xxx；2）调用 defer 函数；3）空的 return。第 1）步和第 3）步是 return 语句生成的指令。

修改函数返回值具体又分`命名返回值`和`非命名返回值`两种情况，在命名返回值的情况中，不管返回值是值类型还是引用类型，都可以在 defer 中直接修改。在非命名返回值的情况，只有引用类型才可以在 defer 中进行修改。不过为了避免歧义，当需要在 defer 中修改返回值时，可以总是将参数定义为命名返回值。

#### 非命名返回值
参考下面两个例子： modifyReturn 以及 modifyReturnPointer，其中前者修改了一个返回值 string，因为 string 是值类型，很明显 defer 中的修改是无效的。后者修改了一个指针，因此是起作用的。
```go
func main() {
	fmt.Println(modifyReturn())         // 打印 "a string"
	fmt.Println(*modifyReturnPointer()) // 打印 "another"
}
func modifyReturn() string {
	a := "a string"
	defer func() {
		a = "another"
	}()
	return a
}
func modifyReturnPointer() *string {
	a := pointer("a string")
	defer func() {
		*a = "another"
	}()
	return a
}
func pointer[T any](a T) *T {
	return &a
}
```
对于这个问题，还需要注意 golang 中， struct 是值类型，除非 struct 的字段包含了指针；interface 类型是引用类型，

#### 命名返回值
命名返回值的情况比较简单，只要在 defer 修改了返回值变量，就会体现到返回值中。
```go
func main() {
	fmt.Println(modifyReturnParam()) // 打印 "another"
}

func modifyReturnParam() (s string) {
	defer func() {
		s = "another"
	}()
	return "origin"
}
```

### 执行顺序
golang 中的 defer 是先声明的后执行，跟 stack 是一样的。