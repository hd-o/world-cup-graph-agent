resource "google_service_account" "runtime" {
  account_id   = "${var.deployment}-runtime"
  display_name = "Managed agent workload identity"
}

resource "google_service_account" "gateway" {
  account_id   = "${var.deployment}-gateway"
  display_name = "Authenticated public gateway identity"
}

resource "google_project_iam_member" "telemetry" {
  project = var.project
  for_each = {
    runtime = google_service_account.runtime.email
    gateway = google_service_account.gateway.email
  }
  role   = "roles/telemetry.writer"
  member = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "quota" {
  project = var.project
  for_each = {
    runtime = google_service_account.runtime.email
    gateway = google_service_account.gateway.email
  }
  role   = "roles/serviceusage.serviceUsageConsumer"
  member = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "secrets" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
  condition {
    title      = "deployment-runtime-secrets"
    expression = "resource.name.startsWith('projects/${data.google_project.target.number}/secrets/${var.deployment}-users-')"
  }
}

resource "google_secret_manager_secret_iam_member" "runtime" {
  secret_id = google_secret_manager_secret.database["runtime"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_identity_platform_tenant" "application" {
  display_name          = var.deployment
  allow_password_signup = true
  client {
    permissions {
      disabled_user_signup   = true
      disabled_user_deletion = true
    }
  }
}

resource "google_project_iam_custom_role" "verification" {
  role_id     = "${replace(var.deployment, "-", "_")}_verification"
  title       = "Gateway token revocation verification"
  permissions = ["firebaseauth.users.get", "identitytoolkit.tenants.get"]
}

resource "google_project_iam_member" "verification" {
  project = var.project
  role    = google_project_iam_custom_role.verification.name
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_cloud_run_v2_service" "agent" {
  name                = "${var.deployment}-agent"
  location            = var.region
  labels              = local.labels
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "600s"
    max_instance_request_concurrency = 4
    scaling {
      min_instance_count = 1
      max_instance_count = 2
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.application.name
        subnetwork = google_compute_subnetwork.agent.name
      }
    }
    containers {
      image = var.agent_image
      resources {
        limits   = { cpu = "2", memory = "4Gi" }
        cpu_idle = false
      }
      dynamic "env" {
        for_each = merge(var.model_environment, {
          ENVIRONMENT            = "demo"
          RUNTIME                = "gcp"
          DEPLOYMENT_NAME        = var.deployment
          DEPLOYMENT_GCP_PROJECT = var.project
          IDENTITY_TENANT        = google_identity_platform_tenant.application.name
          NEO4J_URI              = "bolt://neo4j.worldcup.internal:7687"
          OTEL_SERVICE_NAME      = "${var.deployment}-agent"
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_project_iam_member.secrets,
    google_secret_manager_secret_iam_member.runtime,
    google_dns_record_set.database, google_dns_record_set.apis,
    google_compute_router_nat.database,
    google_compute_network_firewall_policy_association.agent,
    google_compute_network_firewall_policy_rule.database,
    google_compute_network_firewall_policy_rule.google,
    google_compute_network_firewall_policy_rule.openrouter,
    google_compute_network_firewall_policy_rule.denial,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "gateway" {
  name     = google_cloud_run_v2_service.agent.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_cloud_run_v2_service" "gateway" {
  name                = "${var.deployment}-gateway"
  location            = var.region
  labels              = local.labels
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.gateway.email
    timeout                          = "600s"
    max_instance_request_concurrency = 4
    scaling { max_instance_count = 2 }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.application.name
        subnetwork = google_compute_subnetwork.gateway.name
      }
    }
    containers {
      image = var.gateway_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
        # Native OTEL batches must flush after the HTTP response completes.
        cpu_idle = false
      }
      dynamic "env" {
        for_each = {
          GOOGLE_CLOUD_PROJECT = var.project
          GCP_REGION           = var.region
          IDENTITY_TENANT      = google_identity_platform_tenant.application.name
          AGENT_URL            = google_cloud_run_v2_service.agent.uri
          OTEL_SERVICE_NAME    = "${var.deployment}-gateway"
        }
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.gateway, google_project_iam_member.verification, google_dns_record_set.apis]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.gateway.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "runtime" { value = google_cloud_run_v2_service.agent.id }
output "agent" { value = google_cloud_run_v2_service.agent.uri }
output "gateway" { value = google_cloud_run_v2_service.gateway.uri }
output "tenant" { value = google_identity_platform_tenant.application.name }
output "database" { value = google_compute_instance.database.name }
output "database_zone" { value = google_compute_instance.database.zone }
output "database_instance_id" { value = google_compute_instance.database.id }
output "vpc_id" { value = google_compute_network.application.id }
output "nat_gateway_id" { value = google_compute_router_nat.database.id }
output "agent_service_id" { value = google_cloud_run_v2_service.agent.id }
output "gateway_service_id" { value = google_cloud_run_v2_service.gateway.id }
output "firewall_policy_id" { value = google_compute_network_firewall_policy.agent.id }
output "firewall_policy_association_id" { value = google_compute_network_firewall_policy_association.agent.id }
output "agent_subnet_id" { value = google_compute_subnetwork.agent.id }
output "gateway_subnet_id" { value = google_compute_subnetwork.gateway.id }
output "database_firewall_id" { value = google_compute_firewall.bolt.id }

resource "google_apikeys_key" "login" {
  name         = "${var.deployment}-login"
  display_name = "${var.deployment} password login"
  restrictions {
    api_targets { service = "identitytoolkit.googleapis.com" }
  }
}

output "login_key" {
  value     = google_apikeys_key.login.key_string
  sensitive = true
}
