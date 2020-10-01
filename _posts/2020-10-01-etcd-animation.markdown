---
layout:     post
title:      "etcd动画英文摘录"
date:       2020-10-1 10:54:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 分布式
---

有一个介绍etcd基本原理的动画：[Raft - Understandable Distributed Consensus](http://thesecretlivesofdata.com/raft/)，看一遍，耐心理解一下，并将英文记录了下来。手抄或码字没有什么用，只是为了让自己看到每一个字，以及集中注意力。

So What is Distributed Consensus ?

Let's start with an example...

Let's say we have a single node system

For this example, you can think of our node as a database server that stores a single value.

We also have a client that can send a value to the server.

Coming to agreement, or *consensus*, on that value is easy with one node.

But how do we come to consensus if we have multiple nodes ?

That's the problem of *distributed consensus*.

**Raft** is a protocol for implementing distributed consensus.

Let's look at a high level overview of how it works

A node can be in 1 of 3 states: The **Follower** state, the **Candidate** state, or the **Leader** state.

All our nodes start in the follower state.

If followers don't hear from a leader then they can become a candidate.

The candidate then requests votes from other nodes.

Nodes will reply with their vote.

The candidate becomes the leader if it gets votes from a majority of nodes.

This process is called **Leader Election**.

All changes to the system now go through the leader.

Each change is added as entry in the node's log.

This log entry is currently uncommitted so it won't update to the node's value

To commit the entry the node first replicates it to the follower nodes...

then the leader waits untils a majority of nodes have written the entry.

The entry is now committed on the leader node and the node state is "5"

The leader then notifies the followers that the entry is committed.

The cluster has now come to consensus about the system state.

This process is called **Log Replication**.

#### Leader Election

In Raft there are two timeout settings which control elections.

First is the *election  timeout*.

The election timeout is the amount of time a follower waits until becoming a candidate.

The election timeout is randomized to be between 150ms and 300ms.

After the election timeout the follower becomes a candidate and starts a **new election term**...votes for itself...

...and sends out *Request Vote* messages to other nodes.

If the receiving node hasn't voted yet in this term then it votes for the candidates...

...and the node resets its election timeout.

Once a candidate has a majority of votes it becomes leader.

The leader begins sending out *Append Entries* messages to its followers.

These messages are sent in intervals specified by the *heartbeat timeout*.

Followers then respond to each *Append Entries* message.

This election term will continue until a follower stops receiving heartbeats and becomes a candidate.

Let's stop the leader and watch a re-election happen.

Node A is now leader of **term** 2.

Requiring a majority of votes guarantees that only one leader can be elected per term.

If two nodes become candidates at the same time then a split vote can occur.

Let's take a look at a split vote example...

Two nodes both start an election for the same term...

...and each reaches a single follower node before the other.

Now each candidate has 2 votes and can receive no more for this term.

The nodes will wait for a new election and try again.

Node D received a majority of votes in term 5 so it becomes leader.

#### Log Replication

Once we have a leader elected we need to replicate all changes to our system to all nodes.

This is done by using the same *Append Entries* message that was used for heartbeats

Let's walk through the process.

First a client sends a change to leader.

The change is appended to leader's log...

...then the change is send to followers on the next heartbeat.

An entry is committed once a majority of followers acknowledge it...

...and a response is sent to client.

Now let's send a command to increment the value by "2".

Our system value is now updated to "7".

Raft can even stay consistent in the face of network partitions.

Let's add a partition to separate A & B from C, D & E.

Because of our partition we now have two leaders in different terms.

Let's add another client and try to update both leaders.

One client will try to set the value to node B to "3".

Node B cannot replicate to a majority so its log entry stays uncommitted.

The other client will try to set the value of node C to "8".

This will successd because it can replicate to a majority.

Now let's heal the network partition.

Node B will see the higher election term and step down.

Both nodes A & B will roll back their uncommitted entries and match the new leader's log.

Our log is now consistent across our cluster.

**End**

raft 论文：https://raft.github.io/raft.pdf