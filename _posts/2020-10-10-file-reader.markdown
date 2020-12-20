---
layout:     post
title:      "Golang文件读写分片"
date:       2020-12-20 11:56:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

#### 准备工作
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

#### Reader和Writer接口
golang中，最基本的读写接口是Reader以及Writer。其定义如下：
```go
// Reader接口，读取内容到缓冲区p中，返回读取的字节数，以及遇到的错误
// 实现Reader的接口是持有数据的，并把持有的数据写到作为参数的目标缓冲区
type Reader interface {
	Read(p []byte) (n int, err error)
}

// Writer接口，将作为缓冲区的参数的数据写到底层的数据流中，返回写入的数据量以及可能的错误。
// 如果写入的数据量少于len(p)，err一定不是nil的（所以p一定要是全部要写的数据），writer接口不能修改作为参数的slice
type Writer interface {
	Write(p []byte) (n int, err error)
}
```

举例说明，申请一个50K的缓冲区，并读取上面的文件。
```go
	file, err := os.Open("/Users/fake/iotext/file")
	requireNoErr(err)

	buf := make([]byte, 5*10240)
	n, err := file.Read(buf)
	fmt.Println(err)
	fmt.Println(n)
```
输出如下，共读了42552个字节的数据，没有返回错误
```s
<nil>
42552
```
`os.Open`函数以只读的形式打开文件，返回的参数为`os.File`的指针，

#### 分片读取
如果要读取的文件非常大，不能一次加载到内存里，可能要分多次读取。假设我们要将上面的文件分多次读，每次只读1K，实现方式如下。
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
	// 与Reader接口不同的是，ReadAt在读取不够buf size的数据时，会返回错误
	if err == io.EOF {
		fmt.Println("read completed!")
		over = true
    }
    // 如果读不够1K，要截断，不然会写一些脏数据
	if n < size {
		buf = buf[0:n]
	}
	return buf, offset + int64(n), over
}

//从偏移处开始写文件，offset的单位是字节
func write1K(writer io.WriterAt, buf []byte, offset int64) {
	_, err := writer.WriteAt(buf, offset)
	requireNoErr(err)
}

// 计算哈希
func hash(file string) uint32 {
	content, err := ioutil.ReadFile(file)
	requireNoErr(err)
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
	} else {
		fmt.Println("success")
	}
}

```

#### 参考
[Go文件操作大全](https://colobu.com/2016/10/12/go-file-operations/)
[Go语言中的io.Reader和io.Writer以及它们的实现](https://colobu.com/2016/08/29/go-io-Reader-and-io-Writer/)
