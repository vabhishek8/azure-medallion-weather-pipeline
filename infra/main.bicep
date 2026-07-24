// =============================================================================
// Production Azure mapping for the AU weather medallion pipeline.
//
// The pipeline in this repo runs on GitHub Actions + DuckDB because that's
// free and sufficient at this data volume (5 cities x ~1 row/day). This
// template is what the *same* bronze -> silver -> gold design looks like at
// production scale on Azure -- the point isn't "can it run on Azure", it's
// showing the translation from a working local design to a governed,
// observable, IaC-deployed cloud architecture.
//
// Deliberately NOT deployed and left running 24/7: a standing ADF + Synapse
// + Databricks estate for a 5-row/day toy dataset is not a defensible cost
// decision, and shipping a portfolio project that quietly burns a stranger's
// Azure credit is worse than not deploying it. Deploy on demand with:
//
//   az deployment group create \
//     --resource-group rg-weather-pipeline-<env> \
//     --template-file infra/main.bicep \
//     --parameters environment=dev alertEmail=you@example.com
//
// and tear down with `az group delete` when you're done evaluating it.
// =============================================================================

@description('Deployment environment. Drives naming and SKU sizing (dev/sit/uat/prod).')
@allowed(['dev', 'sit', 'uat', 'prod'])
param environment string = 'dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Email address for pipeline-failure alerts.')
param alertEmail string

@description('Base name used to derive all resource names. Lowercase alphanumeric only.')
@minLength(3)
@maxLength(15)
param baseName string = 'weatherelt'

var suffix = '${baseName}${environment}${uniqueString(resourceGroup().id)}'
var tags = {
  project: 'au-weather-medallion-pipeline'
  environment: environment
  managedBy: 'bicep'
  costCentre: 'portfolio-demo'
}

// SKUs scale down hard for dev/sit; this is the "cost of being wrong" lever --
// a prod-sized Databricks/Synapse footprint on a demo project is the kind of
// unreviewed default that quietly triples a monthly bill.
var isProd = environment == 'prod'

// -----------------------------------------------------------------------------
// Storage: ADLS Gen2 (hierarchical namespace on) with bronze/silver/gold
// containers. This directly replaces the local data/{bronze,silver,gold}/
// folders in the repo -- same three-zone contract, durable + versioned.
// -----------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: take('st${suffix}', 24)
  location: location
  tags: tags
  sku: {
    name: isProd ? 'Standard_ZRS' : 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true // hierarchical namespace = ADLS Gen2, not flat blob
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }

  resource blobServices 'blobServices' = {
    name: 'default'
    properties: {
      deleteRetentionPolicy: {
        enabled: true
        days: 14
      }
    }

    resource bronze 'containers' = {
      name: 'bronze'
      properties: { publicAccess: 'None' }
    }
    resource silver 'containers' = {
      name: 'silver'
      properties: { publicAccess: 'None' }
    }
    resource gold 'containers' = {
      name: 'gold'
      properties: { publicAccess: 'None' }
    }
  }
}

// -----------------------------------------------------------------------------
// Key Vault: holds the Open-Meteo endpoint config and any future API keys.
// ADF and Databricks read secrets via managed identity, not connection
// strings baked into pipeline JSON -- avoids the classic "secret committed
// to source control" failure mode.
// -----------------------------------------------------------------------------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('kv-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// -----------------------------------------------------------------------------
// Data Factory: orchestration layer, replaces the GitHub Actions cron.
// A system-assigned managed identity is granted Storage Blob Data Contributor
// scoped to this storage account only -- least privilege, no shared keys.
// -----------------------------------------------------------------------------
resource dataFactory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: take('adf-${suffix}', 24)
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
}

@description('Storage Blob Data Contributor role definition ID (built-in).')
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource adfStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, dataFactory.id, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    principalId: dataFactory.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageBlobDataContributorRoleId
    )
  }
}

// -----------------------------------------------------------------------------
// Synapse Serverless SQL: the cloud analogue of the DuckDB gold-layer query
// in src/gold.py. Serverless (pay-per-query-TB-scanned) rather than a
// dedicated SQL pool -- a dedicated pool billed hourly for a 5-row/day
// dataset is the textbook "operationally complex and needlessly expensive"
// mistake this design explicitly avoids.
// -----------------------------------------------------------------------------
resource synapseWorkspace 'Microsoft.Synapse/workspaces@2021-06-01' = {
  name: take('synw-${suffix}', 24)
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    defaultDataLakeStorage: {
      accountUrl: storage.properties.primaryEndpoints.dfs
      filesystem: 'gold'
    }
    sqlAdministratorLogin: 'synapseadmin'
    managedResourceGroupName: 'rg-${suffix}-synapse-managed'
  }
}

resource synapseStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, synapseWorkspace.id, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    principalId: synapseWorkspace.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageBlobDataContributorRoleId
    )
  }
}

resource synapseFirewallAllowAzure 'Microsoft.Synapse/workspaces/firewallRules@2021-06-01' = {
  parent: synapseWorkspace
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// -----------------------------------------------------------------------------
// Observability: Log Analytics + Application Insights capture ADF pipeline
// run outcomes; an action group + alert rule notify on failure instead of
// requiring someone to notice a red X in the portal. This is the direct
// cloud equivalent of the repo's `pipeline failed` log line + non-zero
// exit code that fails the GitHub Actions job.
// -----------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: take('log-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: isProd ? 90 : 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: take('appi-${suffix}', 24)
  location: location
  tags: tags
  kind: 'other'
  properties: {
    Application_Type: 'other'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: take('ag-${suffix}', 24)
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'weatheralert'
    enabled: true
    emailReceivers: [
      {
        name: 'pipeline-owner'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource pipelineFailureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-adf-pipeline-failed-${environment}'
  location: 'global'
  tags: tags
  properties: {
    severity: 1
    enabled: true
    scopes: [dataFactory.id]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'PipelineFailedRuns'
          metricName: 'PipelineFailedRuns'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output storageAccountName string = storage.name
output dataFactoryName string = dataFactory.name
output synapseWorkspaceName string = synapseWorkspace.name
output keyVaultName string = keyVault.name
output dataFactoryPrincipalId string = dataFactory.identity.principalId
