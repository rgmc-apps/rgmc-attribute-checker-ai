# Initial Instructions
- Analyze this repository - https://github.com/rgmc-apps/AttributeChecker. determine the main functions and other conditions. make it a web service and reference the tech stack on this repository - https://github.com/rgmc-apps/sbic-invoice-splitter
- Restructure the config file by putting the variables in a environment variable that can be declared in the google cloud run configuration:
    - API_KEY - transfer to os.environ
    - GPT_VERSION - transfer to os.environ
    - GPT_LIMIT - transfer to os.environ
    - SCRIPT_VERSION - transfer to os.environ
    - GENERATE_TEXT - make it an option in the web page for the users to choose
    - PROMPT_V2 - transform into an editable text box (excluding the main prompt instruction)
- remove the .exe files and replace it with a choice or a button for processing
- copy the method of upload and download of files in this repository - https://github.com/rgmc-apps/sbic-invoice-splitter

# Main functions
 - Create a web service that allows the users to upload multiple files. analyze the file based on the premade prompt that will be sent to the openai api
 - Make the prompt attributes editable. Provide a textbox where the users can provide a specific attribute and a corresponding description
 - have an option to generate either an xls file, text file, csv file or both based on the response of the openai api endpoint
    - For the xls file : make the chosen attributes as table headers. also add these columns:
        1. Picture - Place the uploaded picture in the column
        2. Product Title - The generated file name on the beginning of the prompt response
        3. Product Description - The Full product description generated
    - For the text file : Place the prompt response without the response headers. Make the product title as the filename of the text file
    - For the csv file : same as the method for the xls file. just replace the picture column as the original photo's file name.
