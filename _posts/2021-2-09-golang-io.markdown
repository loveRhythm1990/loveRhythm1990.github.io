---
layout:     post
title:      "一些golang日常读写操作"
date:       2021-02-09 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - go
---

记录一些工具方法，避免每次都google，当然最好是能直接看文档

##### 读写
`WriteFile`和`ReadFile`在读写完成后，会自动把文件close掉。`WriteFile`会覆盖文件内容。（追加可以在打开文件时，使用`os.O_APPEND`标志）
```go
    err := ioutil.WriteFile("test.txt", []byte("Hi\n"), 0666)
    if err != nil {
        log.Fatal(err)
    }

    data, err := ioutil.ReadFile("test.txt")
    if err != nil {
        log.Fatal(err)
    }
```
控制读写的字节数，golang有一些内置的函数，可以控制读写的字符数
```go
	f, err := os.Open("file.txt")
	if err != nil {
		fmt.Println(err)
	}

    // 读数据到buf，最多读2个
	buf := make([]byte, 2)
	n, err := f.Read(buf)
	if err != nil {
		fmt.Println(err)
	}

	fmt.Println("read bytes: ", n)
	fmt.Println(string(buf))

    // 把buf读满
    // file.Read()可以读取一个小文件到大的byte slice中，
    // 但是io.ReadFull()在文件的字节数小于byte slice字节数的时候会返回错误
    byteSlice := make([]byte, 2)
    numBytesRead, err := io.ReadFull(file, byteSlice)

    byteSlice := make([]byte, 512)
    minBytes := 8
    // io.ReadAtLeast()在不能得到最小的字节的时候会返回错误，但会把已读的文件保留
    numBytesRead, err := io.ReadAtLeast(file, byteSlice, minBytes)
    if err != nil {
        log.Fatal(err)
    }
```

##### 哈希相关
哈希可以用在校验文件、数据块中，有两种方式：1）输入数据，返回哈希值。2）返回一个writer，往writer里写数据
```go
package main

import (
	"fmt"
	"hash/crc32"
	"io"
	"os"
)

func main() {
	f, err := os.Open("file.txt")
	if err != nil {
		panic(err)
	}
	defer f.Close()

	h := crc32.NewIEEE()
	n, err := io.Copy(h, f)
	if err != nil {
		panic(err)
	}
	fmt.Printf("read bytes: %d\n", n)
	fmt.Println(h.Sum32())
}
```
此外还可以用`hash/fnv`生成hash。跟上述方式一致：
```go
	data := []byte("abcdef")
	hash := fnv.New32()
	hash.Write(data)
	return hash.Sum32()
```

##### 检查文件是否存在
```go
fileInfo, err := os.Stat("test.txt")
if err != nil {
    if os.IsNotExist(err) {
        log.Fatal("File does not exist.")
    }
}
```
另外还有个`os.Lstat`方法，这个方法使用方式跟，`os.Stat`一致，区别是如果文件是个软连接，`Lstat`返回软连接的`FileInfo`，并不会跟随软连接。

##### 硬链接
硬链接跟原来的文件指向同一个inode，指向同一个inode的文件的文件名之间没有区别，看不出是一个硬链接。
使用`find ./ -inum 1234`命令，可以查看inode为1234的所有文件名。

另外，在linux里，目录是不能创建硬链接的
```go
// 硬链接
err := os.Link("file.txt", "a_hard_link")
if err != nil {
    fmt.Println(err.Error())
}
// 软连接
err = os.Symlink("original.txt", "original_sym.txt")
if err != nil {
    fmt.Println(err.Error())
}
```

##### 创建临时文件
写UT的时候，经常需要创建临时文件
```go
	// 在系统临时文件夹中创建一个临时文件夹
	tempDirPath, err := ioutil.TempDir("", "myTempDir")
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		os.RemoveAll(tempDirPath)
	}()

	// 在临时文件夹中创建临时文件
	tempFile, err := ioutil.TempFile(tempDirPath, "myTempFile.txt")
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		// 文件需要关闭
		tempFile.Close()
	}()
	// ... 做一些操作 ...
```

##### 编解码
将一些元数据序列化后写入文件
```go
	f, err := os.Create("file.txt")
	if err != nil {
        log.Fatal(err)
    }
    // 编码：Encoder的参数是一个writer
	err = json.NewEncoder(f).Encode(meta)
	if err != nil {
		log.Warnf("failed to encode meta, err: %v", err)
	}
	f.Close()
```
解码示例如下：
```go
	f, err := os.Open("file.txt")
	if err != nil {
        log.Fatal(err)
	}
	defer f.Close()

	err = json.NewDecoder(f).Decode(meta)
	if err != nil {
		log.Fatal(err)
	}
```

##### 列举目录下的所有文件
有两种方式一种是，返回目录下所有文件的`os.FileInfo`，另一种方式是返回一个名字列表，也是`[]string`，这两种方式都是按照文件名字排好序的。
```go
    // 返回[]os.FileInfo
	fis, err := ioutil.ReadDir(path)
	if err != nil {
		log.Warnf("failed to read dir path %s, err %v", s.path, err)
	}

	// 同样返回[]os.FileInfo
	dir, err := os.Open(fullPath)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	files, err := dir.Readdir(-1)
	
	// 返回[]string
	dir, err := os.Open(fullPath)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	files, err := dir.Readdirnames(-1)
```

##### 遍历目录
首先定义一个`filepath.WalkFunc`，然后调用`filepath.Walk`，前者的签名为：

`type WalkFunc func(path string, info os.FileInfo, err error) error`

第一个参数是要访问的目录或文件，第二个参数是该path对应的`FileInfo`，这个是一个接口类型，第三个参数是处理该path时的错误，如果err不为nil，则第二个参数`info`是nil。这个方法有点绕，可以看一下具体怎么实现的：
```go
// 这个Walk是递归的warpper，本身不是递归的
func Walk(root string, walkFn WalkFunc) error {
	info, err := os.Lstat(root)
	if err != nil {
        // Lstat的时候出现了错误，把错误传递给walkFn，root path保持不变
		err = walkFn(root, nil, err)
	} else {
        // 没有错，开始递归
		err = walk(root, info, walkFn)
	}
	if err == SkipDir {
		return nil
	}
	return err
}

// 递归处理文件
func walk(path string, info os.FileInfo, walkFn WalkFunc) error {
	if !info.IsDir() {
		// 如果当前path不是目录（是一个普通文件），直接对其调用walkFn
		// 也就是处理叶子节点
		return walkFn(path, info, nil)
	}

    // 是个目录，获取该目录下的所有文件，（已经排好序的）
    names, err := readDirNames(path)
    
    // 首先处理根目录，由此看是先序遍历
    err1 := walkFn(path, info, err)
    // 只要err或者err1不为nil，都不会对目录下的文件进行遍历，直接返回
	if err != nil || err1 != nil {
		return err1
	}
	// 下面开始递归了，依次处理每个子目录
	for _, name := range names {
		filename := Join(path, name)
		// 这里为了测试，使用自定义的函数变量lstat代替了os.Lstat，这样测试可以注入其他实现
		fileInfo, err := lstat(filename)
		if err != nil {
			// lstat的错误不用nil，看一下walkFn要不要处理此错误，并直接返回（不是SkipDir错误）
			// 由此看，遍历的时候，只要出现错误就返回了
			if err := walkFn(filename, fileInfo, err); err != nil && err != SkipDir {
				return err
			}
		} else {
			// 递归调用
			err = walk(filename, fileInfo, walkFn)
			if err != nil {
				if !fileInfo.IsDir() || err != SkipDir {
					return err
				}
			}
		}
	}
	return nil
}
```
看一下怎么使用，使用比较简单，如下：
```go
	var walkFn filepath.WalkFunc = func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			fmt.Println("I found a dir: ", info.Name())
		} else {
			fmt.Println("I found a file: ", info.Name())
		}
		return err
	}

	filepath.Walk("/Users/jc/go/src/tp", walkFn)
```


#### 参考：
[译 Go文件操作大全](https://colobu.com/2016/10/12/go-file-operations/)