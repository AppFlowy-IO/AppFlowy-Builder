require 'xcodeproj'
Dir.chdir(__dir__)
project = Xcodeproj::Project.new('UpgradeValidation.xcodeproj')
test = project.new_target(:ui_test_bundle, 'ReleasedUpgradeUITests', :osx, '14.0')
test.add_file_references([project.main_group.new_file('ReleasedUpgradeUITests.swift')])
test.build_configurations.each do |config|
  config.build_settings.merge!(
    'SWIFT_VERSION' => '5.0', 'PRODUCT_BUNDLE_IDENTIFIER' => 'org.appflowy.qa.nativeupgradeuitests',
    'GENERATE_INFOPLIST_FILE' => 'YES', 'CODE_SIGN_IDENTITY' => '-',
    'CODE_SIGN_STYLE' => 'Automatic', 'ENABLE_USER_SCRIPT_SANDBOXING' => 'YES'
  )
end
project.save
scheme = Xcodeproj::XCScheme.new
scheme.add_build_target(test)
scheme.add_test_target(test)
scheme.save_as(project.path, 'ReleasedUpgradeUITests', true)
