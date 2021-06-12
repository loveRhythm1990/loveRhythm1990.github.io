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

参考：

[TCP: About FIN_WAIT_2, TIME_WAIT and CLOSE_WAIT](https://benohead.com/blog/2013/07/21/tcp-about-fin_wait_2-time_wait-and-close_wait/)

[Wireshark过滤规则使用](https://blog.51cto.com/laoyinga/1767613)


