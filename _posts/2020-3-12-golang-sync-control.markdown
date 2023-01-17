---
layout:     post
title:      "Golang 使用 channel 进行同步控制"
date:       2020-3-12 16:56:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 设计模式
---

同步控制是指协调多个 goroutine 之间的运行时机，比如想控制两个 goroutine A、B 的执行顺序，A 运行完之后，B 再运行。一个最常见的问题（或者说面试题）是：多个 goroutine 交替打印数字。已知 channel 是 golang 中同步控制的利器，那怎么用 channel 来解决这个问题呢？

channel 是一个队列（在学习 golang 时，我先入为主的将 channel 理解为 java 中的阻塞队列，至今也感觉这个理解没什么不妥），作为一个队列就需要有人填充元素，有人消费元素，所以也是一个消费者生产者模型。假设我们要求两个 goroutine A、B 交替打印数字，那么问题可以用下面模型解决：A goroutine 绑定一个 channel，它需要从这个 channel 中拿到 token 才能执行(A作为这个 channel 的消费者)；B goroutine 绑定一个 channel，它需要从这个 channel 中拿到 token 才能执行；其中 A 是 B 绑定的 channel 的生产者，B 是 A 绑定的 channel 的生产者。模型示意图如下：
![java-javascript](/pics/sync-controller-in-go.jpg){:height="50%" width="50%"}

下面的代码是，要求有 N 个 goroutine 交替打印数字，一直打印到 100。代码较为简洁，可以作为此设计模式的参考：
```go
// n 个 goroutine 交替打印数字，打印到 100
func printNumber(n int) {
	chans := make([]chan struct{}, n)
	for i := 0; i < n; i++ {
		chans[i] = make(chan struct{}, 1)
	}
	chans[0] <- struct{}{}
	cur := 1
	wg := &sync.WaitGroup{}
	for i := 0; i < n; i++ {
		wg.Add(1)
		go goroutineI(&cur, i, chans, wg)
	}
	wg.Wait()
}

func goroutineI(cur *int, i int, chans []chan struct{}, wg *sync.WaitGroup) {
	next := (i + 1) % len(chans)
loop:
	for {
		select {
		case <-chans[i]:
			if *cur <= 100 {
				fmt.Printf("goroutine: %d, print: %d\n", i+1, *cur)
				*cur = *cur + 1
			} else {
				close(chans[next])
				break loop
			}
			chans[next] <- struct{}{}
		}
	}
	wg.Done()
}
```