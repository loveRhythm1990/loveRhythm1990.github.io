---
layout:     post
title:      "实现文件分片传输的一个小例子"
date:       2020-12-20 11:56:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Golang
---

实现文件分片传输的一个小例子。
### 准备工作
找一个文件，一共42552字节。
```s
fake@Mac iotext % ls -l file  
-rw-r--r--  1 fake  staff  42552 12 20 12:16 file
```
查看文件内容hash值。
```go
content, err := ioutil.ReadFile(fileName)
requireNoErr(err)
h := fnv.New32a()
h.Write(content)
fmt.Println(h.Sum32())
```
输出为：1898344699


### 分片读取
如果要读取的文件非常大，不能一次加载到内存里，可能要分多次读取。假设我们要将上面的文件分多次读，每次只读 1K，实现方式如下。

这里还有两个 Golang 读写接口，这两个接口跟 Reader、Writer 不同之处是可以从指定位置 `offset` 开始读写数据，这个也是分片读写的关键。

* **io.ReaderAt**: 定义为 `ReadAt(buf, offset)(int, error)`，这个接口读数据到 buf 中，如果读到的数据量少于 `len(p)`，会返回错误，比如数量量不够 `len(p)` 时，会返回 `io.EOF` 错误。即使返回了错误，缓冲区的数据仍然是可以消费的，这个可以根据实际读到的字节数来消费。
* **io.WriteAt**: 从指定位置写数据，如果数据没写完，会报错。

```go
const (
	fileName = "/Users/fake/iotext/file"
	targetfile = "/Users/fake/iotext/target"
)

// 返回读取的数据和下次偏移，以及是否读完了
func read1K(reader io.ReaderAt, offset int64) ([]byte, int64, bool) {
	size := 1024
	buf := make([]byte, size)
	n, err := reader.ReadAt(buf, offset)
	over := false
	if err == io.EOF { // 遇到 io.EOF 错误了，可以视为一个提示信息，表示数据读完了
		fmt.Println("read completed!")
		over = true
    }
	if n < size {     // 如果读不够1K，要截断，不然会写一些脏数据
		buf = buf[0:n]
	}
	return buf, offset + int64(n), over
}

//从偏移处开始写文件，offset的单位是字节
func write1K(writer io.WriterAt, buf []byte, offset int64) {
	_, _ = writer.WriteAt(buf, offset)
}

// 计算哈希
func hash(file string) uint32 {
	content, _ := ioutil.ReadFile(file)
	h := fnv.New32a()
	h.Write(content)
	return h.Sum32()
}

func main() {
	file, _ := os.Open(fileName)
	target, _ := os.OpenFile(targetfile, os.O_WRONLY, 0666)

	var offset, next int64
	var buf []byte
	var over bool
	for {
        // 循环读取，每次读1k
		buf, next, over = read1K(file, offset)
		if len(buf) > 0 {
			write1K(target, buf, offset)
		}
		if over {
			break
		}
		offset = next
	}
	hash1 := hash(fileName)
	hash2 := hash(targetfile)
	if hash1 != hash2 {
		panic(fmt.Sprintf("hash, expect: %d, got: %d", hash1, hash2))
	}
	fmt.Println("success")
}
```

#### 参考
[Go文件操作大全](https://colobu.com/2016/10/12/go-file-operations/)
[Go语言中的io.Reader和io.Writer以及它们的实现](https://colobu.com/2016/08/29/go-io-Reader-and-io-Writer/)
