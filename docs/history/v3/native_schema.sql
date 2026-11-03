-- Original Alibaba field semantics; missing ILP fields become NULL.
-- Execute only in an independently chosen experiment database/table namespace.
CREATE TABLE machine_usage (
    timestamp TIMESTAMP,
    machine_id SYMBOL,
    cpu_util_percent LONG,
    mem_util_percent LONG,
    mem_gps DOUBLE,
    mkpi LONG,
    net_in DOUBLE,
    net_out DOUBLE,
    disk_io_percent DOUBLE
) TIMESTAMP(timestamp) PARTITION BY DAY WAL;
