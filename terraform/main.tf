terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name prefix for all resources."
  type        = string
  default     = "pr-scanner"
}

variable "environment" {
  description = "Deployment environment (lets each teammate bring up an isolated stack)."
  type        = string
  default     = "dev"
}

variable "max_receive_count" {
  description = "Times a job is retried before SQS routes it to the DLQ."
  type        = number
  default     = 3
}

variable "lambda_timeout_seconds" {
  description = "Webhook Lambda timeout."
  type        = number
  default     = 10
}

variable "existing_lambda_role_name" {
  description = "If set, reuse this existing IAM role for the Lambda instead of creating one. AWS Academy Learner Lab: set to \"LabRole\" (you cannot create IAM roles there)."
  type        = string
  default     = ""
}

variable "db_engine_version" {
  description = "PostgreSQL engine version for the metadata RDS instance."
  type        = string
  default     = "16.13"
}

variable "db_instance_class" {
  description = "RDS instance class. db.t3.micro is Free Tier eligible and fine for the Learner Lab."
  type        = string
  default     = "db.t3.micro"
}

variable "scanner_cpu" {
  description = "Fargate task CPU units for the scanner (256 = 0.25 vCPU)."
  type        = number
  default     = 1024
}

variable "scanner_memory" {
  description = "Fargate task memory (MiB) for the scanner."
  type        = number
  default     = 2048
}

variable "scanner_image_tag" {
  description = "Tag of the scanner image in ECR to run."
  type        = string
  default     = "latest"
}

variable "github_token" {
  description = "GitHub fine-grained PAT for posting PR comments. Set via tfvars or -var; ignored on later applies."
  type        = string
  default     = "PLACEHOLDER_SET_ME"
  sensitive   = true
}

variable "alert_email" {
  description = "Email address that receives DLQ failure alerts. Requires one-time subscription confirmation."
  type        = string
  default     = ""
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
