---
layout:     post
title:      "golang context sample case"
date:       2020-03-29 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

占坑，

分析golang中context的使用。
首先贴个《go核心编程》中的小例子

```go
package main

import (
	"context"
	"fmt"
	"time"
)

// define a new type include a Context Field
type otherContext struct {
	context.Context
}

func main() {

	// 使用context.Background()构建一个withCancel类型的上下文
	ctxa, cancel := context.WithCancel(context.Background())
	go work(ctxa, "work1")

	// 使用WithDeadline包装前面的上下文对象ctxa
	tm := time.Now().Add(time.Second * 3)
	ctxb, _ := context.WithDeadline(ctxa, tm)

	go work(ctxb, "work2")

	// 使用WithValue包装前面的上下文对象ctxb
	oc := otherContext{ctxb}
	ctxc := context.WithValue(oc, "key", "I am value")

	go workWithValue(ctxc, "work3")

	// 故意 sleep 10秒，让work2, work3先他退出
	time.Sleep(10 * time.Second)

	// 显式调用work1的cancel方法通知其退出
	cancel()

	// 等待work1打印退出信息
	time.Sleep(time.Second * 5)
	fmt.Println("main stop")
}

// do something
func work(ctx context.Context, name string) {
	for {
		select {
		case <-ctx.Done():
			fmt.Printf("%s get msg to cancal\n", name)
			return
		default:
			fmt.Printf("%s is running \n", name)
			time.Sleep(time.Second)
		}
	}
}

// 等待前端的退出通知，并试图获取context传递的数据
func workWithValue(ctx context.Context, name string) {
	for {
		select {
		case <-ctx.Done():
			fmt.Printf("%s get msg to cancel\n", name)
		default:
			value := ctx.Value("key").(string)
			fmt.Printf("%s is running value=%s\n", name, value)
			time.Sleep(time.Second)
		}
	}
}
```
