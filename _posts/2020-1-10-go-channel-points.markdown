---
layout:     post
title:      "golang中channel知识点"
subtitle:   " \"channel\""
date:       2020-1-10 21:29:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

本文算是读书笔记，读的书是《Go并发编程》


#### 通道操作的特性
通道是在多个goroutine之间传递数据和同步数据的重要手段，而对通道**操作**本身也是**同步**的。在同一时刻，仅有个goroutine能向一个通道发送元素值，同时也仅有一个goroutine能从它那里接收元素值。在通道中，各个元素值都是**严格**按照发送到此的先后顺序排列的，最早被发送至通道的元素会最先被接收。

因此通道相当于一个FIFO的消息队列。此外，通道中的元素值都具有原子性，是不可被分割的。通道中的每一个元素值都只可能被某一个goroutine接收，已被接收的元素值会立刻从通道中删除。

简单总结就是：**对于channel，操作是同步的，数据是原子的**。

如果读取操作阻塞，goroutine会进入`Gwaiting`状态，直到strChan中有新元素可取时才会别唤醒。
```go
strChan := make(chan string, 3)
elem := <-strchan
```
在写法`elem, ok := <-strChan`中，变量`ok`表示操作是否成功（并不等价于通道是否关闭）。
若通道关闭时，通道内没有元素，任何阻塞操作都会立刻返回，此时ok值为false，表示通道关闭了，操作失败。如果通道内还有元素，则虽然通道关闭了，还是会读取成功，ok值为true，直到通道中的元素读完时，才返回操作失败（因为通道关闭了，并不会阻塞），看下面例子：
```go
package main

import (
	"fmt"
	"sync"
)

func main()  {
	wg := &sync.WaitGroup{}
	intChan := make(chan int, 100)
	for i:=1;i<=100;i++ {
		intChan <- i
	}

	close(intChan)

	wg.Add(1)
	go readChan(intChan, wg)
	wg.Wait()
}

func readChan(intChan chan int, wg *sync.WaitGroup) {
	for {
		// read channel
		v, ok := <- intChan
		if !ok {    // operation fail
			break
        }
        //会将所有1-100的数打印出来
		fmt.Println(v, ok)
	}
	wg.Done()
}
```
#### happen before
不太懂这个意义是什么，可能解释了数据的写入与读取都是原子的行为，并且会发生赋值行为（元数据的一个副本），具体为：
###### 带缓冲区的channel
* 发送数据会将数据拷贝到缓冲区，或者正在阻塞的一个goroutine的内存地址
* 接收方会读取一个副本
* 发送一定在接收之前，在通道完全复制一个元素值之前，任何goroutine都不可能从它那里接收到这个元素值的副本。
###### 不带缓冲区的channel
先来的一方要等后来的一方完成之后才完成。比如，我要写元素，必须要等接收者完成读取之后，我才算写完成。我要读元素，读操作成功必须在写操作成功之后完成。可以认为是同步的吧。

#### 单向通道
如果函数的参数为单向通道，实际传入时，仍然传入一个双向通道，Go会依据参数的声明，自动把它转换为单向通道，这是一个强约束，在该函数中从通道接收元素会造成编译错误。

#### for语句与channel
通道中没有任何元素值时，for语句所在的goroutine也会陷入阻塞，阻塞的具体位置会在其中的range子语句处。for语句会不断尝试从通道中接收元素值，直到通道关闭。在通道关闭时，如果通道中已无元素值，那么这条for语句的执行会立即结束。而当此时通道中还有遗留的元素值时，for语句可以继续把它们读完。这与普通的操作行为一致。

#### select语句
select语句是一种仅能用于通道接收和发送操作的专用语句。如果select所有case都不满足条件，并且没有default case，那么当前goroutine就会一直被阻塞于此，直到至少有一个case中的发送或者接收操作可以立即进行为止。如果同时又多个case满足条件，那么运行时系统会通过一个**伪随机**的算法选择一个case，其他的case则被忽略。

在开始执行select语句时，所有跟在case关键字右边的发送语句或接收语句中的通道表达式和元素表达式都会先求值（求值的顺序是**从左到右，自上而下**）。无论它们所在的case语句是否有可能被选择都是这样的。参考下面代码：
```go
package main

import "fmt"

var intChan1 chan int
var intChan2 chan int
var channels = []chan int{intChan1, intChan2}
var numbers = []int{1, 2, 3, 4, 5}

func main()  {
	select {
        // eval from left to right, for up to down
	case getChan(0) <- getNumber(0):
		fmt.Println("1th case is selected")
	case getChan(1) <- getNumber(1):
		fmt.Println("2th case is selected")
	default:
		fmt.Println("default")
	}
}

func getNumber(i int) int {
	fmt.Printf("numbers[%d]\n", i)
	return numbers[i]
}

func getChan(i int) chan int {
	fmt.Printf("channel[%d]\n", i)
	return channels[i]
}
```
输出为：
```bash
channel[0]
numbers[0]
channel[1]
numbers[1]
default
```
其他特性有待补充