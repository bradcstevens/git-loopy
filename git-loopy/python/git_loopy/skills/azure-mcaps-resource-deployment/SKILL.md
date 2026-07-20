---
name: azure-mcaps-resource-deployment
description: This skill is a guide to setting the right tagging and API authentication settings with a Microsoft internal MCAPS subscription to support access to Microsoft Foundry and related resources for local develpment and automation.
---

# Azure Resource Deployment

## 1. Always tag Azure resources with the security-ignore exemption tag

When deploying ANY resource to Azure (via Azure CLI, Bicep, ARM, Terraform, azd, Pulumi, or any SDK):

- Run the following Azure skills:
  1. Prepare project: `/azure-prepare`
  2. Before deploying: `/azure-validate` 
  3. Deployment execution: `/azure-deploy`
- **Always include the tag `SecurityControl=Ignore`** on every resource and resource group you create or update.
- This tag exempts the resource from the tenant-level MCAPS governance policies (notably `CognitiveServices_LocalAuth_Modify` and `CosmosDB_LocalAuth_Modify`) which otherwise force `disableLocalAuth: true`.
- For IaC templates, set the tag explicitly in the `tags` property — do not rely solely on subscription-level auto-tag policies, because IaC redeploys often replace tag arrays.
- For `az` CLI commands, pass `--tags SecurityControl=Ignore` (merging with any other tags the user requested).
- When updating existing resources, use `az tag update --operation merge` to avoid overwriting existing tags.

Example (Bicep):
```bicep
resource example 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: {
    SecurityControl: 'Ignore'
  }
  // ...
}
```

Example (Azure CLI):
```bash
az cognitiveservices account create \
  --name myresource \
  --resource-group myrg \
  --kind AIServices \
  --sku S0 \
  --location eastus \
  --tags SecurityControl=Ignore
```



## 2. Default to API key authentication on Microsoft Foundry resources

When creating or configuring any Microsoft Foundry / Azure AI Services / Cognitive Services resource:

- Run the `azure-ai` and `/microsoft-foundry` skills.

- **Set `disableLocalAuth: false` by default** so API key authentication is the primary authentication mechanism.
- Combine this with the `SecurityControl=Ignore` tag (rule #1) so the MCAPS `CognitiveServices_LocalAuth_Modify` policy does not flip `disableLocalAuth` back to `true`.
- Do not silently switch the project to Entra-only auth unless the user explicitly asks for keyless / managed-identity authentication.
- When generating Foundry SDK / client code samples, default to API-key-based clients (e.g., `AzureKeyCredential`) rather than `DefaultAzureCredential`, unless the user requests otherwise.

Example (Bicep):
```bicep
resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  tags: {
    SecurityControl: 'Ignore'
  }
  properties: {
    disableLocalAuth: false
    customSubDomainName: name
  }
}
```

Example (Azure CLI / REST patch on existing resource):
```bash
az rest --method patch \
  --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>?api-version=2024-10-01" \
  --body '{"properties": {"disableLocalAuth": false}}'
```



## Notes

- Always confirm both rules above are honored when generating IaC, deployment scripts, or runbook commands that provision Azure resources.
- If a user explicitly opts out (e.g., "use managed identity only" or "don't tag this"), respect their explicit instruction and skip the relevant rule for that request.
