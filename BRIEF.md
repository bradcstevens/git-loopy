# Brief

- on git-loopy init and when invoked, git-loopy will do a lookup to the latest and greatest models available to it.
- All models available to git-loopy become in scope for analysis.
- On first run of git-loopy, it will run the assessment and output the results into a .git-loopy directory. 
- The analysis results will be output the results into a .git-loopy directory. 
- git-loopy will conduct an assessment.
- git-loopy outputs the results of the assement, detailing its analysis as well as conclusions made.
- The assessment is an extremely deep review and analysis of the project.
- These outputs of the assessment will be used to determine the best model as well as its (if supported) its available corresponding reasoning levels to be set in git-loopy's routing config per task classification. e.g. The results of the analysis provide a conclusion regarding the best model to set in git-loopy's config for the route task.
- On first run, git-loopy will run commands to reveal what models are available thefn creating/updating a log that records all of the models available at the time git-loopy ran.
- On git-loopy start, git-loopy should refer to its current model routing settings from the last previous session it ran. If no previous session is found, set git-loopy's config routing per task type after performing an intitial assessment over the available models and reasoning levels to determine what models as well as their reasoning levels should be set in git-loopy's config, treating this as a new install. 
- For each model set in git-loopy's router config as well as if models avalable have changed between runs, previous assessments and conclusions drawn after analysis will be appended with the latest model assesment results. 
- The initial assessment and conclusions drawn in the analysis will take the longest to complete. 
- All additonal assessments and conclusions drawn from the analysis of the assessment will be compared to previous assessment analysis conclusions. 
- individual model's will be updated in git-loopy's config to use the most capable model for the task that balances capability, quality, and total end-to-end response time
