---
layout:     post
title:      "golang中的生产者消费者"
date:       2020-1-10 21:29:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

本文算是读书笔记，读的书是《Go语言核心编程李文塔》

#### goroutine一些特性
* 调度器不能保证多个goroutine的执行次序
* 没有父子goroutine的概念，所有的goroutine都是平等地被调度和执行的
* Go程序在执行时会单独为main函数创建一个goroutine，遇到其他go关键字再去创建其他goroutine
* Go没有暴露goroutine id给用户，所以不能在一个goroutine里面显示的操作另一个goroutine，不过runtime包提供了一些函数访问和设置goroutine的相关信息。

#### 通道何时会发生panic？
* 向已经关闭的通道写数据会发生panic，最佳实践是由写入者关闭通道，能最大限度地避免向已经关闭的通道写数据而导致的panic
* 重复关闭的通道会导致panic

#### 生产者消费者
目前就能想到这么简单的case，以后有复杂的的时候再补充，补充一个常见的golang面试题：两个goroutine交替打印奇偶数字，并且按照大小顺序输出。使用两个不带缓冲区的channel作为通信，每个goroutine在打印数字前，需要先向channel中写一个数据。

```go
package main

import (
	"fmt"
	"sync"
)

func main() {
	oddChan, evenChan := make(chan struct{}), make(chan struct{})
	odd, even := make(chan int, 5), make(chan int, 5)

	wg := &sync.WaitGroup{}

	wg.Add(1)
	go producer(odd, even)
	go printOdd(oddChan, evenChan, odd, wg)
	go printEven(oddChan, evenChan, even)

	evenChan <- struct{}{}

	wg.Wait()
}

func producer(odd, even chan int) {
	for i := 0; i <= 9; i++ {
		if i%2 == 0 {
			even <- i
		} else {
			odd <- i
		}
	}
}

func printOdd(oddChan, evenChan chan struct{}, odd chan int, wg *sync.WaitGroup) {
	for v := range odd {

		_ = <-oddChan

		fmt.Println(v)
		if v == 9 {
			wg.Done()
		}

		evenChan <- struct{}{}
	}
}

func printEven(oddChan, evenChan chan struct{}, even chan int) {
	for v := range even {

		_ = <-evenChan

		fmt.Println(v)

		oddChan <- struct{}{}
	}
}

```