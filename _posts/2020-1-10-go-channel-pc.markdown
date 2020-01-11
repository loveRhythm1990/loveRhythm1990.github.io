---
layout:     post
title:      "golang中的生产者消费者"
subtitle:   " \"channel生产者消费者模型\""
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
目前就能想到这么简单的case，以后有复杂的的时候再补充
```go
package main

import (
	"fmt"
	"sync"
	"time"
)

func main()  {
	intChane := make(chan int, 1)
	wg := &sync.WaitGroup{}
	
	wg.Add(1)
	go produce(intChane, wg)
	wg.Add(1)
	go consume(intChane, wg)

	wg.Wait()
}

// WaitGroup Must be a pointer
func produce(ch chan<- int, wg *sync.WaitGroup)  {
	for i:=0; i<5;i++ {
		ch <- i
	}
	// close when write over, or consume will never stop
	close(ch)
	wg.Done()
}

func consume(ch <-chan int, wg *sync.WaitGroup)  {
	for v := range ch {
		v *= 2
		fmt.Println("received:", v)
		time.Sleep(time.Second)
	}
	wg.Done()
}
```