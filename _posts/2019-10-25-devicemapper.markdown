---
layout:     post
title:      "实现自己的device mapper"
subtitle:   " \"翻译\""
date:       2019-10-25 22:55:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 存储
---

一篇教你怎么实现自己的DeviceMapper驱动的文章，下面代码在Ubuntu 16.04及上通过（14.04上也通过，18.04未通过）。
原文链接：[Writing Your Own Device Mapper Target](http://techgmm.blogspot.com/p/writing-your-own-device-mapper-target.html)

### Device Mapper
在Linux内核中，Device Mapper是将底层物理设备映射成上层逻辑设备的框架，它是LVM2的基础。在内核中它通过一个一个模块化的 target driver 插件实现对 IO 请求的过滤或者重新定向等工作，当前已经实现的 target driver 插件包括软 raid、软加密、逻辑卷条带、多路径、镜像、快照等。整个 device mapper机制由两部分组成--内核空间的 device mapper 驱动、用户空间的device mapper 库以及它提供的 dmsetup 工具，lvm2是通过device mapper库与内核中的代码交互的，另外device mapper库的代码在lvm2的源代码里，写的还是比较复杂。

DeviceMapper在内核中的代码大概如下图所示，也许这张图不够详细具体，我在本文的最后把DeviceMapper英文维基百科中的图片贴上了：

![java-javascript](/img/in-post/devicemapper/Picture1.png)

### Device Mapper Target
一个Target就是实现Device Mapper框架的一个实例，目前有Linear、DM-Crypt、镜像、快照等。我们还可以自定义Target，下面就是实现一个自定义驱动的例子，虽然很简单，但是提供了实现思路，对于理解DeviceMapper的理解也很有好处，真的非常感谢作者。在内核中，**Device Mapper主要有三个概念：mapped device、映射表、target device**，对于这些概念，IBM的这篇文章写的很详细，真的值得读很多遍[Linux 内核中的 Device Mapper 机制](https://www.ibm.com/developerworks/cn/linux/l-devmapper/index.html)。
* mapped device, 对应内核代码dm.c文件定义的mapped_device结构，包括该mapped device相关的锁，注册的请求队列和一些内存池以及它所对应*映射表*的指针域。

* 映射表，对应dm_table.c文件中定义的dm_table结构，该结构包含一个dm_target结构数组，dm_target结构具体描述了mapped_device到它某个target device的映射关系。

*  Target_type 结构主要包含了 target device 对应的 target driver 插件的名字、 定义的构建和删除该类型target device的⽅法、 该类target device对应的IO请求重映射和结束IO的⽅法等。 ⽽表⽰具体的target device的域是dm_target中的private域， 该指针指向mapped device所映射的具体target device对应的结构

下面这个图中介绍了这三个概念涉及到的数据结构，对于实现自己的回调函数，主要是实现自己的Target Type类型，Target Type中提供了很多回调函数，比如:
* crt:构造函数
* dtr:析构函数
* map:映射函数等

我们实现这些函数就好了，但是还是需要理解内核中的一些概念，比如BIO，这个希望自己以后能慢慢掌握。对于这些数据结构的讲解，《存储技术原理分析_基于Linux 2.6内核源代码》这本书中也有很好的说明，不得不说，这也是一本神书，目前应该已经绝版，只能买二手的，真希望有时间能好好研究。
![java-javascript](/img/in-post/devicemapper/Picture2.jpg)

[Linux 内核中的 Device Mapper 机制](https://www.ibm.com/developerworks/cn/linux/l-devmapper/index.html)这篇文章中提到实现一个Drive类型需要实现的方法有：

1. 构建target device 的方法；
2. 删除target device 的方法；
3. Target的映射IO请求的方法；
4. Target结束IO请求的方法；
5. 暂停target device读写的方法；
6. 恢复target device读写的访问；
7. 获取当前target device状态的访问；
8. Target 处理用户消息的方法；

一般来说，简单的话，只需要实现前三个就好了，下面的代码就是实现了前三个，而且work的很好。下面就是源代码了，本来想翻译注释的，怕自己理解不够准确，引起误导，还是不要画蛇添足了。

**basic_target.c**

```c
#include<linux/module.h>
#include<linux/kernel.h>
#include<linux/init.h>
#include <linux/bio.h>
#include <linux/device-mapper.h>

/* This is a structure which will store  information about the underlying device 
*  Param:
* dev : underlying device
* start:  Starting sector number of the device
*/
struct my_dm_target {
        struct dm_dev *dev;  /* 对应物理设备的dm_dev结构指针 */
        sector_t start;      /* 在该物理设备中以扇区为单位的偏移地址start */
};




/* This is map function of basic target. This function gets called whenever you get a new bio
 * request.（每当收到bio请求的时候就会被调用）.The working of map function is to map a particular bio request to the underlying device.（map 函数的作用是将bio映射到底层的物理设备） 
 *The request that we receive is submitted to out device so  bio->bi_bdev points to our device.
 * We should point to the bio-> bi_dev field to bdev of underlying device. Here in this function,
 * we can have other processing like changing sector number of bio request, splitting bio etc. 
 * （我们可以改变请求的sector编号，split bio等，将对mapped device的请求的bio进行分割）
 *
 *  Param : 
 *  ti : It is the dm_target structure representing our basic target
 *  bio : The block I/O request from upper layer
 *  map_context : Its mapping context of target.
 *
 *  Return values from target map function:
 *  DM_MAPIO_SUBMITTED :  Your target has submitted the bio request to underlying request
 *  DM_MAPIO_REMAPPED  :  Bio request is remapped, Device mapper should submit bio.  
 *  DM_MAPIO_REQUEUE   :  Some problem has happened with the mapping of bio, So 
 *                        re queue the bio request. So the bio will be submitted 
 *                        to the map function  
 */
static int basic_target_map(struct dm_target *ti, struct bio *bio,union map_info *map_context)
{
        // 参数 dm_target的private执行目标target（或自定义target）
        struct my_dm_target *mdt = (struct my_dm_target *) ti->private;
        printk(KERN_CRIT "\n<<in function basic_target_map \n");

        // 修改bio底层的物理设备为target中的底层设备
        bio->bi_bdev = mdt->dev->bdev;

        if((bio->bi_rw & WRITE) == WRITE)
                printk(KERN_CRIT "\n basic_target_map : bio is a write request.... \n");
        else
                printk(KERN_CRIT "\n basic_target_map : bio is a read request.... \n");
        // 提交bio
        submit_bio(bio->bi_rw,bio);


        printk(KERN_CRIT "\n>>out function basic_target_map \n");       
        return DM_MAPIO_SUBMITTED;
}


/* This is Constructor Function of basic target 
 *  Constructor gets called when we create some device of type 'basic_target'.
 *  （构造函数在每次创建basic_target类型的设备的时候调用）
 *  So it will get called when we execute command 'dmsetup create'
 *  This  function gets called for each device over which you want to create basic 
 *  target. Here it is just a basic target so it will take only one device so it  
 *  will get called once. 
 */
static int 
basic_target_ctr(struct dm_target *ti,unsigned int argc,char **argv)
{
        struct my_dm_target *mdt;
        unsigned long long start;

        printk(KERN_CRIT "\n >>in function basic_target_ctr \n");

        if (argc != 2) {
                printk(KERN_CRIT "\n Invalid no.of arguments.\n");
                ti->error = "Invalid argument count";
                return -EINVAL;
        }

        // 为自定义dm_target数据结构申请空间
        mdt = kmalloc(sizeof(struct my_dm_target), GFP_KERNEL);

        if(mdt==NULL)
        {
                printk(KERN_CRIT "\n Mdt is null\n");
                ti->error = "dm-basic_target: Cannot allocate linear context";
                return -ENOMEM;
        }       

        // 从第一个参数读取开始的start sector
        if(sscanf(argv[1], "%llu", &start)!=1)
        {
                ti->error = "dm-basic_target: Invalid device sector";
                goto bad;
        }

        mdt->start=(sector_t)start;
        

        /* dm_get_table_mode 
         * Gives out you the Permissions of device mapper table. 
         * This table is nothing but the table which gets created
         * when we execute dmsetup create. This is one of the
         * Data structure used by device mapper for keeping track of its devices.
         *
         * dm_get_device 
         * The function sets the mdt->dev field to underlying device dev structure.
         */

    
        if (dm_get_device(ti, argv[0], dm_table_get_mode(ti->table), &mdt->dev)) {
                ti->error = "dm-basic_target: Device lookup failed";
                goto bad;
        }

        // 将自定义my_dm_target结构添加到dm_target的private域
        ti->private = mdt;


        printk(KERN_CRIT "\n>>out function basic_target_ctr \n");                       
        return 0;

  bad:
        kfree(mdt);
        printk(KERN_CRIT "\n>>out function basic_target_ctr with errorrrrrrrrrr \n");           
        return -EINVAL;
}

/*
 * This is destruction function
 * This gets called when we remove a device of type basic target. The function gets 
 * called per device. 
 */
static void basic_target_dtr(struct dm_target *ti)
{
        struct my_dm_target *mdt = (struct my_dm_target *) ti->private;
        printk(KERN_CRIT "\n<<in function basic_target_dtr \n");        
        dm_put_device(ti, mdt->dev);
        // 释放数据结构占用的空间
        kfree(mdt);
        printk(KERN_CRIT "\n>>out function basic_target_dtr \n");               
}


/*

 * This structure is fops for basic target.

 */
static struct target_type basic_target = {
        
        // 这个basic_target是我们使用dmsetup create
        // 创建设备时的名字
        .name = "basic_target",
        .version = {1,0,0},
        .module = THIS_MODULE,
        .ctr = basic_target_ctr,
        .dtr = basic_target_dtr,
        .map = basic_target_map,
};
        
/*---------Module Functions -----------------*/
/* 注册模块相关的代码 */
// 自定义模块的初始化
static int init_basic_target(void)
{
        int result;
        // dm_register_target这个函数是重点
        result = dm_register_target(&basic_target);
        if(result < 0)
                printk(KERN_CRIT "\n Error in registering target \n");
        return 0;
}

// 自定义模块的销毁
static void cleanup_basic_target(void)
{
        dm_unregister_target(&basic_target);
}

// 调用module_init初始化自定义模块
module_init(init_basic_target);
module_exit(cleanup_basic_target);
MODULE_LICENSE("GPL");
```

在上面代码中`dm_register_target`这个函数是linux提供的注册一个dm_target的函数，是linux提供的，供我们使用的。

另外还有一个比较重要的问题是**device mapper工作在哪一层？感觉是在通用块设备层**

在《极客时间，块设备下》中说道：*不管是直接IO，还是缓存IO，最后都到了submit_bio里面，submit_bio会调用generic_make_request*，`submit_bio`的函数签名为：
```c
/**
 * submit_bio - submit a bio to the block device layer for I/O
 * @bio: The &struct bio which describes the I/O
 */
blk_qc_t submit_bio(struct bio *bio)
{
......
  return generic_make_request(bio);
}
```
bio是通⽤块层I/O请求的数据结构，表⽰上层提交的I/O请求，⼀个bio包含多个page，这些page必
须对应磁盘上⼀段连续的空间。由于⽂件在磁盘上并不连续存放，⽂件I/O提交到块设备之前，极有可能被拆成多个bio结构；

![java-javascript](/img/in-post/devicemapper/iostack.jpeg)


这部分写的无头无脑，日后整理。


**Makefile**
```c
obj-m +=basic_target.o

all:
        make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules

clean:
        make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean
```

**USAGE**
```c
echo 0 <size_of_device> basic_target /Path/to/your/device 0 | dmsetup create my_basic_dm_device
```


**Compilation**
```c
make
insmod basic_target.ko
```

**Setup**


Lets create a temp file of 2GB for device.

`dd if=/dev/zero of=/tmp/disk1 bs=512 count=20000`

Attach loop device to this file.

`losetup /dev/loop6 /tmp/disk1`

Lets create device with 'basic_target'

`echo 0 20000 basic_target /dev/loop6 0|dmsetup create my_basic_target_device`


可以在/var/log/messages或/var/log/klog或者dmesg中看到构造函数被调用的日志。.
新产生的逻辑设备为： `/dev/mapper/my_basic_target_device`

### 参考

[Writing Your Own Device Mapper Target](http://techgmm.blogspot.com/p/writing-your-own-device-mapper-target.html)

[Linux 内核中的 Device Mapper 机制](https://www.ibm.com/developerworks/cn/linux/l-devmapper/index.html)

### 附录
**The Linux Storage Stack Diagram**
![java-javascript](/img/in-post/devicemapper/IO_stack_of_the_Linux_kernel.png)
