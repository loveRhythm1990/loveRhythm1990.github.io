---
layout:     post
title:      "使用 Context 设置 Http 请求超时"
date:       2021-6-12 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - golang
---

使用`Context`限制 Http 请求的超时时间。客户端代码如下。这里设置的超时时间是1秒钟，过了一秒钟，如果服务端还没有返回请求，则客户端主动发送 `fin` 报文断开请求。
```go
func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	req, err := http.NewRequest("GET", "http://localhost:8888/", nil)
	if err != nil {
		log.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req.WithContext(ctx))
	if err != nil {
		// return err
		log.Fatal(err)
	}
	// 如果发生超时，这个 resp 为 nil，因此不能进行 close，会 panic
	defer resp.Body.Close()

	io.Copy(os.Stdout, resp.Body)
}
```
服务端代码如下，服务端是等待两秒钟之后，才处理请求的，在等待的过程中，客户端超时了。
```go
func Index(w http.ResponseWriter, r *http.Request, _ httprouter.Params) {
	time.Sleep(time.Second * 2)
	fmt.Fprint(w, "Welcome!\n")
	fmt.Println("I over")
}

func main() {
	router := httprouter.New()
	router.GET("/", Index)
	log.Fatal(http.ListenAndServe(":8888", router))
}
```
整个过程的报文交互如下，客户端使用的端口为 58188，服务端使用的端口为 8888，刚开始的三行为 tcp 建立连接的过程。连接建立之后的交互如下：
1. 客户端 15：31 发送 Http Get 请求，报文的大小为 95，下一个报文序号为 96，点开 `GET / HTTP /1.1` 查看 TCP 报文详情。
    ![java-javascript](/img/in-post/common/detail_tcp.png)
2. 服务端对此请求进行了ack，`Ack=96`，并开始在服务端处理业务。
3. 过了一秒钟，也就是 16：31，客户端超时了，发送了一个 fin 报文。
4. 服务端对客户端的 fin 报文进行了回应，客户端进入 fin_wait_2 状态。
5. 又过了一秒钟，17：31，服务端业务处理完成，向服务端发送数据 Welcome!。
6. 客户端收到数据后，发送了 `RST` 报文给服务端。不要这些数据了。

从截图看，客户端发了两次 RST 报文给服务端，一次是服务端发送数据后，一次是服务端发送 FIN 报文后。

![java-javascript](/img/in-post/common/context.png)

这里还有个问题，客户端把连接关闭了（发送了 fin 报文），服务端写数据会报错吗？根据[tcp 连接的建立与断开](https://loverhythm1990.github.io/2021/04/02/tcp-0/)，四次挥手的过程，及时有一方发送了 fin 报文，另一方仍然是可以发送数据的，看上去是不会报错的，在我们的代码验证中，也是这个样子的.
```go
func Hello(w http.ResponseWriter, r *http.Request, ps httprouter.Params) {
	// sleep 一段时间，等待客户端超时
	time.Sleep(time.Second * 10)
	fmt.Println("hello handler has been called in server")
	_, err := fmt.Fprintf(w, "hello, %s!\n", ps.ByName("name"))
	// 这个地方，客户端已经超时了，但是服务端发送数据时，不会报错。
	// 这里输出的错误为 nil
	fmt.Printf("server write err: %v", err)
}
```
另外根据[Go语言TCP Socket编程](https://tonybai.com/2015/11/17/tcp-programming-in-golang/)，服务端调用 `Write()` 只是把数据发送到了自己这段的内核缓冲区，数据发送由内核做的，所以不会报错，这里即使客户端调用 `conn.Close()` 把连接关闭了，服务端调用 `Write()` 仍然不会报错。

那还有个问题，客户端发生了超时，发送了 `fin` 报文，那还能接收数据吗？从 golang 角度来看，应该是不可能了（因为超时之后，返回了一个 nil 的 response），看下面例子：
```go
	resp, err := http.DefaultClient.Do(req.WithContext(ctx))
	if err != nil {
		fmt.Printf("request do err: %v\n", err)
		// 这里发生了报错，并不 return 或者 panic
		//log.Fatal(err)
		//return
	}
	// 等待几秒，这个时候，服务端应该已经发送数据了
	time.Sleep(time.Second * 6)

	// 结果发现这个 resp 为 nil，既然为 nil，那肯定不能从 resp.Body 里读数据了，会panic.
	// 这个地方输出 resp: <nil>
	fmt.Printf("resp: %v", resp)
	defer resp.Body.Close()

	io.Copy(os.Stdout, resp.Body)
```

参考：

[TCP: About FIN_WAIT_2, TIME_WAIT and CLOSE_WAIT](https://benohead.com/blog/2013/07/21/tcp-about-fin_wait_2-time_wait-and-close_wait/)

[Wireshark过滤规则使用](https://blog.51cto.com/laoyinga/1767613)


