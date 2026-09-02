# Brief: explaining this morning's export delay to the support lead

The support lead, who is not an engineer, asked you in chat:

> Customers are asking why their exports were slow this morning. What do I
> tell them?

You handled the incident. Everything below is verified.

## What happened

- Between 09:14 and 10:02 today, customer exports (CSV and PDF) sat waiting
  instead of running within the usual two minutes. The longest any single
  export waited was 51 minutes.
- Cause of the slowdown: a configuration change deployed at 09:14 set the
  export worker pool size, `export_workers.max_workers`, to 2 instead of the
  usual 8. With a quarter of the usual capacity, exports piled up; the
  backlog peaked at 340 waiting jobs.
- At 10:02 the setting was put back to 8. The backlog cleared by 10:40.
- Every export requested during the window completed. You compared the job
  table: 1,212 export jobs created between 09:00 and 10:40, all 1,212 in
  status `completed`. None failed, none were lost, no data was affected.

## What is not known

- Why the configuration change set the value to 2. Someone on the team is
  investigating; there is no timeline for that answer.
- Whether or when a preventive change will ship. Nothing has been committed
  to.

## About the reader

The support lead does not know what a worker pool, a queue, or a
configuration deploy is, and has no use for the setting's name. They want
something they can relay to customers.
