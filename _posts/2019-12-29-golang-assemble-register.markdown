---
layout:     post
title:      "golang寄存器相关[入门]"
subtitle:   " \"helloworld\""
date:       2019-12-29 21:40:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

> 介绍golang汇编中的几个寄存器，golang汇编对我来说来说太难了，一点一点啃，我学习golang汇编的动力在于了解golang运行原理，目前还没有这样的体验。

本文都是记录网上已经有的东西，并贴出了相关出处链接，请直接跳到相关链接阅读，[golang高级编程（曹春晖）](https://chai2010.cn/advanced-go-programming-book/ch3-asm/ch3-02-arch.html)。

### golang中的伪寄存器
[golang汇编](https://lrita.github.io/2017/12/12/golang-asm/)有四个伪寄存器，用来维护上下文，特殊标识等作用的。
* FP(Frame pointer): arguments and locals
* PC(Program counter): jumps and branches
* SB(Static base pointer): global symbols
* SP(Stack pointer): top of stack

[四个伪寄存器和X86/AMD64的内存和寄存器的相互关系如下图](https://chai2010.cn/advanced-go-programming-book/ch3-asm/ch3-02-arch.html)，在AMD64环境，伪PC寄存器其实是IP指令计数器寄存器的别名。伪FP寄存器对应的是函数的帧指针，一般用来访问函数的**参数和返回值**。伪SP栈指针对应的是当前函数栈帧的底部（不包括参数和返回值部分），一般用于定位**局部变量**。伪SP是一个比较特殊的寄存器，因为还存在一个同名的SP真寄存器。真SP寄存器对应的是栈的顶部，一般用于定位调用其它函数的参数和返回值。

当需要区分伪寄存器和真寄存器的时候只需要记住一点：伪寄存器一般需要一个标识符和偏移量为前缀，如果没有标识符前缀则是真寄存器。比如(SP)、+8(SP)没有标识符前缀为真SP寄存器，而a(SP)、b+8(SP)有标识符为前缀表示伪寄存器

![java-javascript](/img/in-post/golang-register/gr.png){:height="80%" width="80%"}

### 关于AMD64（x86-64）寄存器
在使用汇编语言之前必须要了解对应的CPU体系结构。下面是X86/AMD架构图。
左边是内存部分是常见的内存布局。其中text一般对应代码段，用于存储要执行指令数据，代码段一般是只读的。然后是rodata和data数据段，数据段一般用于存放全局的数据，其中rodata是只读的数据段。而heap段则用于管理动态的数据，stack段用于管理每个函数调用时相关的数据。在汇编语言中一般重点关注text代码段和data数据段，因此Go汇编语言中专门提供了对应TEXT和DATA命令用于定义代码和数据。

中间是X86提供的寄存器。寄存器是CPU中最重要的资源，每个要处理的内存数据原则上需要先放到寄存器中才能由CPU处理，同时寄存器中处理完的结果需要再存入内存。X86中除了状态寄存器FLAGS和指令寄存器IP两个特殊的寄存器外，还有AX、BX、CX、DX、SI、DI、BP、SP几个通用寄存器。在通用寄存器中BP和SP是两个比较特殊的寄存器：其中BP用于记录当前函数帧的开始位置，和函数调用相关的指令会隐式地影响BP的值；SP则对应当前栈指针的位置，和栈相关的指令会隐式地影响SP的值；而某些调试工具需要BP寄存器才能正常工作。

右边是X86的指令集。CPU是由指令和寄存器组成，指令是每个CPU内置的算法，指令处理的对象就是全部的寄存器和内存。我们可以将每个指令看作是CPU内置标准库中提供的一个个函数，然后基于这些函数构造更复杂的程序的过程就是用汇编语言编程的过程。
![java-javascript](/img/in-post/golang-register/x86.png){:height="80%" width="80%"}

### 参考：
[golang高级编程（https://chai2010.cn/advanced-go-programming-book/ch3-asm/ch3-02-arch.html）](https://chai2010.cn/advanced-go-programming-book/ch3-asm/ch3-02-arch.html)

[golang汇编(https://lrita.github.io/2017/12/12/golang-asm/)](https://lrita.github.io/2017/12/12/golang-asm/)

