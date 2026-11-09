# CloudWatch dashboard silvercare-overview (docs/features/cloudwatch-dashboard.md).
#
# Free: the always-free tier covers 3 custom dashboards of up to 50 metrics each, and
# viewing your own dashboard in the console isn't charged for GetMetricData. So: only
# AWS/* metrics, no log or custom (Lambda) widgets, never shared. 40 metrics below.
# Every metric is written out (no `for` expressions) so
# scripts/tests/test_dashboard_infra.py can count them from the text.

locals {
  dashboard_region = data.aws_region.current.region
  dashboard_period = 300

  # Consumed capacity is a Sum per period; divided by the period it compares with the
  # provisioned units per second, drawn as horizontal lines.
  dashboard_tables = {
    registry = { name = module.registry_table.name, read = module.registry_table.read_capacity, write = module.registry_table.write_capacity }
    latest   = { name = module.latest_table.name, read = module.latest_table.read_capacity, write = module.latest_table.write_capacity }
    alerts   = { name = module.alerts_table.name, read = module.alerts_table.read_capacity, write = module.alerts_table.write_capacity }
    results  = { name = module.results_table.name, read = module.results_table.read_capacity, write = module.results_table.write_capacity }
  }
}

resource "aws_cloudwatch_dashboard" "overview" {
  dashboard_name = "silvercare-overview"

  dashboard_body = jsonencode({
    start = "-PT3H"
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "API Gateway: requests and errors"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.api.id, { label = "Requests" }],
            ["AWS/ApiGateway", "4xx", "ApiId", aws_apigatewayv2_api.api.id, { label = "4xx" }],
            ["AWS/ApiGateway", "5xx", "ApiId", aws_apigatewayv2_api.api.id, { label = "5xx" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "API Gateway: latency p95 (ms)"
          region = local.dashboard_region, period = local.dashboard_period, stat = "p95", view = "timeSeries"
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiId", aws_apigatewayv2_api.api.id, { label = "Latency p95" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "IoT Core: messages and connections"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/IoT", "PublishIn.Success", "Protocol", "MQTT", { label = "Messages in" }],
            ["AWS/IoT", "RulesExecuted", { label = "Rules executed" }],
            ["AWS/IoT", "Connect.Success", "Protocol", "MQTT", { label = "Connects" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Lambda: all functions"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Invocations", { label = "Invocations" }],
            ["AWS/Lambda", "Errors", { label = "Errors" }],
            ["AWS/Lambda", "Throttles", { label = "Throttles" }],
            ["AWS/Lambda", "ConcurrentExecutions", { label = "Concurrent (max)", stat = "Maximum", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title  = "Lambda errors: alert path"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", module.ingest_lambda["device_health"].function_name],
            ["AWS/Lambda", "Errors", "FunctionName", module.ingest_lambda["telemetry"].function_name],
            ["AWS/Lambda", "Errors", "FunctionName", module.ingest_lambda["critical_alert"].function_name],
            ["AWS/Lambda", "Errors", "FunctionName", module.alert_processor.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", module.history.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", module.watchdog.function_name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title  = "SQS: messages waiting"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Maximum", view = "timeSeries"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.alerts.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.history.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.alerts_dlq.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.critical_alert_dlq.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.history_dlq.name],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 18, width = 12, height = 6
        properties = {
          title  = "DynamoDB: throttle events"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", module.registry_table.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", module.registry_table.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", module.latest_table.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", module.latest_table.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", module.alerts_table.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", module.alerts_table.name],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", module.results_table.name],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", module.results_table.name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 18, width = 12, height = 6
        properties = {
          title  = "Simulator EC2"
          region = local.dashboard_region, period = local.dashboard_period, view = "timeSeries"
          metrics = [
            ["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.simulator.id, { label = "CPU % (avg)", stat = "Average" }],
            ["AWS/EC2", "StatusCheckFailed", "InstanceId", aws_instance.simulator.id, { label = "Status check failed", stat = "Maximum", yAxis = "right" }],
          ]
        }
      },
      # One capacity widget per table, 2 metrics each.
      {
        type = "metric", x = 0, y = 24, width = 6, height = 6
        properties = {
          title  = "DynamoDB ${local.dashboard_tables.registry.name}: capacity used per second"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", local.dashboard_tables.registry.name, { id = "r", visible = false }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", local.dashboard_tables.registry.name, { id = "w", visible = false }],
            [{ expression = "r/PERIOD(r)", label = "Read used" }],
            [{ expression = "w/PERIOD(w)", label = "Write used" }],
          ]
          annotations = { horizontal = [
            { label = "Read limit", value = local.dashboard_tables.registry.read },
            { label = "Write limit", value = local.dashboard_tables.registry.write },
          ] }
        }
      },
      {
        type = "metric", x = 6, y = 24, width = 6, height = 6
        properties = {
          title  = "DynamoDB ${local.dashboard_tables.latest.name}: capacity used per second"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", local.dashboard_tables.latest.name, { id = "r", visible = false }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", local.dashboard_tables.latest.name, { id = "w", visible = false }],
            [{ expression = "r/PERIOD(r)", label = "Read used" }],
            [{ expression = "w/PERIOD(w)", label = "Write used" }],
          ]
          annotations = { horizontal = [
            { label = "Read limit", value = local.dashboard_tables.latest.read },
            { label = "Write limit", value = local.dashboard_tables.latest.write },
          ] }
        }
      },
      {
        type = "metric", x = 12, y = 24, width = 6, height = 6
        properties = {
          title  = "DynamoDB ${local.dashboard_tables.alerts.name}: capacity used per second"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", local.dashboard_tables.alerts.name, { id = "r", visible = false }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", local.dashboard_tables.alerts.name, { id = "w", visible = false }],
            [{ expression = "r/PERIOD(r)", label = "Read used" }],
            [{ expression = "w/PERIOD(w)", label = "Write used" }],
          ]
          annotations = { horizontal = [
            { label = "Read limit", value = local.dashboard_tables.alerts.read },
            { label = "Write limit", value = local.dashboard_tables.alerts.write },
          ] }
        }
      },
      {
        type = "metric", x = 18, y = 24, width = 6, height = 6
        properties = {
          title  = "DynamoDB ${local.dashboard_tables.results.name}: capacity used per second"
          region = local.dashboard_region, period = local.dashboard_period, stat = "Sum", view = "timeSeries"
          metrics = [
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", local.dashboard_tables.results.name, { id = "r", visible = false }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", local.dashboard_tables.results.name, { id = "w", visible = false }],
            [{ expression = "r/PERIOD(r)", label = "Read used" }],
            [{ expression = "w/PERIOD(w)", label = "Write used" }],
          ]
          annotations = { horizontal = [
            { label = "Read limit", value = local.dashboard_tables.results.read },
            { label = "Write limit", value = local.dashboard_tables.results.write },
          ] }
        }
      },
    ]
  })
}
