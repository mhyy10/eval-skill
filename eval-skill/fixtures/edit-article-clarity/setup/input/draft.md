# Why our deploy pipeline is slow

In conclusion, the pipeline is slow. But let me start at the beginning.

As mentioned earlier, and I will mention it again, the tests take a long
time. The tests take a long time because they run sequentially. Sequential
execution means one after another. When things run one after another, the
total time is the sum of all the times.

Also the build step. The build step is also slow. It rebuilds everything
every time even when nothing changed in most of the packages. Caching
would help. Caching is when you store the result of a computation so you
do not have to redo the computation.

As mentioned earlier, tests are slow. We could parallelize them. We could
also cache the build. In conclusion, the pipeline is slow because tests
are sequential and the build does not cache, and as mentioned earlier
these are the two main reasons.
